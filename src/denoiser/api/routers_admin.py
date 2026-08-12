"""Operator-facing status: signing keys, credentials, usage meters, tenant API keys, and the metrics scrape.

Split out of `api.main`, which held eleven unrelated concerns in 1,500 lines:
every parallel feature branch touched that one file and every one of them
conflicted. A pure move — no handler below is changed, and the routes are the
same paths at the same methods.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from denoiser import runtime
from denoiser.api.auth import (
    require_role,
)
from denoiser.api.observability import authorize_scrape, metrics_response
from denoiser.api.pagination import MAX_PAGE_SIZE
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.logging import get_logger
from denoiser.storage.db import Tenant, User, get_db
from denoiser.telemetry.ebpf_collector import EBPFCollector
from denoiser.utils.time import iso_utc, utcnow

logger = get_logger(__name__)

router = APIRouter(tags=["Admin"])

#: Started and stopped by the application lifespan in `api.main`; this
#: module only reads what it has collected.
ebpf_agent = EBPFCollector()


@router.get("/admin/signing-keys")
def signing_key_status(current_user: User = Depends(require_role(["ADMIN"]))):
    """Which JWT signing key is active and which retired keys are still accepted.

    An operator rolling the secret needs to confirm the new key took effect on
    every replica before dropping the old one from JWT_SECRET_KEY_PREVIOUS.
    Key ids are truncated hashes — the secrets themselves are never exposed.
    """
    from denoiser.api.keys import get_keyring

    return get_keyring().describe()


@router.get("/admin/credentials")
def credential_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Rotation state of the long-lived credentials, without exposing any of them.

    Covers what /admin/signing-keys does for the JWT key: whether each shared
    secret is set, whether a superseded value is still being accepted, and when
    this tenant's API key was last rotated.
    """
    from denoiser.api.credentials import describe_static_rotation
    from denoiser.api.keys import get_keyring
    from denoiser.api.tenancy import describe_scim_token, normalise_domains

    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    now = utcnow()
    overlap_ends = tenant.api_key_previous_expires_at if tenant else None

    return {
        "jwt_signing_keys": get_keyring().describe(),
        "ingest_api_key": describe_static_rotation("INGEST_API_KEY"),
        "scim_bearer_token": describe_static_rotation("SCIM_BEARER_TOKEN"),
        # This organisation's own SCIM credential and the email domains that
        # route federated identities to it. Neither the token nor any other
        # organisation's domains are exposed.
        "organisation": {
            "name": tenant.name if tenant else None,
            "sso_domains": normalise_domains(tenant.sso_domains) if tenant else [],
        },
        "tenant_scim_token": describe_scim_token(tenant),
        "tenant_api_key": {
            "configured": bool(tenant and tenant.api_key),
            "last_rotated_at": iso_utc(tenant.api_key_rotated_at) if tenant else None,
            "previous_key_accepted_until": (
                iso_utc(overlap_ends) if overlap_ends and overlap_ends > now else None
            ),
        },
    }


@router.get("/admin/usage")
def usage_meters(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
    scope: TenantScope = Depends(tenant_scope),
):
    """Per-day ingest volume for the caller's tenant, and their retention tier.

    The meters were being written by a task no deployment ever started, and no
    endpoint read them — so metered usage existed only as a table definition.
    """
    from denoiser.storage.db import BillingMeter
    from denoiser.workers.billing_worker import (
        DEFAULT_RETENTION_DAYS,
        RETENTION_DAYS_BY_TIER,
    )

    days = max(1, min(days, 365))
    since = utcnow() - timedelta(days=days)

    meters = (
        scope.query(BillingMeter)
        .filter(BillingMeter.date >= since)
        .order_by(BillingMeter.date.desc())
        .all()
    )
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    tier = (tenant.tier if tenant else None) or "free"

    return {
        "tier": tier,
        "retention_days": RETENTION_DAYS_BY_TIER.get(tier.lower(), DEFAULT_RETENTION_DAYS),
        "window_days": days,
        "totals": {
            "logs": sum(m.total_logs_ingested or 0 for m in meters),
            "bytes": sum(m.total_bytes_ingested or 0 for m in meters),
            "traces": sum(m.total_traces_ingested or 0 for m in meters),
        },
        "daily": [
            {
                "date": iso_utc(m.date),
                "logs": m.total_logs_ingested or 0,
                "bytes": m.total_bytes_ingested or 0,
                "traces": m.total_traces_ingested or 0,
            }
            for m in meters
        ],
    }


@router.post("/admin/usage/recalculate")
def recalculate_usage(current_user: User = Depends(require_role(["ADMIN"]))):
    """Re-run today's metering now instead of waiting for the nightly pass.

    Retention is left alone: deleting data is the scheduled pass's job, not a
    side effect of asking for a fresh number.
    """
    from denoiser.workers.billing_worker import aggregate_billing

    return aggregate_billing(enforce_retention=False)


class RotateApiKeyRequest(BaseModel):
    # 0 revokes the old key immediately — the correct choice for a leak.
    overlap_hours: int = Field(default=24, ge=0, le=720)


@router.post("/admin/tenant/api-key/rotate")
def rotate_tenant_key(
    payload: RotateApiKeyRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Issue a new API key for the caller's tenant. The key is returned once.

    The superseded key keeps working for `overlap_hours` so log shippers can be
    updated one at a time; pass 0 to cut it off immediately.
    """
    from denoiser.api.credentials import rotate_tenant_api_key

    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    overlap = payload.overlap_hours if payload else 24
    new_key = rotate_tenant_api_key(db, tenant, overlap_hours=overlap)
    return {
        "status": "rotated",
        "api_key": new_key,
        "overlap_hours": overlap,
        "previous_key_accepted_until": iso_utc(tenant.api_key_previous_expires_at),
        "warning": "Store this key now — it is not retrievable again.",
    }


@router.post("/admin/tenant/api-key/revoke-previous")
def revoke_previous_tenant_key(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """End the overlap early, once every shipper carries the new key."""
    from denoiser.api.credentials import revoke_previous_api_key

    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    revoked = revoke_previous_api_key(db, tenant)
    return {"status": "revoked" if revoked else "no_previous_key"}


@router.get("/internal/metrics")
async def internal_metrics(request: Request):
    """Prometheus exposition of SemanticOS's own request rate, errors and latency.

    Gated on METRICS_TOKEN — see `authorize_scrape`. Left unauthenticated this
    hands out the deployment's route inventory and traffic profile.
    """
    authorize_scrape(request)

    # Dead-letter depth is read here rather than tracked in-process: the
    # records are quarantined by the ingestion worker, a different pod, so the
    # count only exists in Redis. Silent data loss with no series to alert on
    # is what makes it dangerous.
    from denoiser.workers.dead_letter import read_counters
    from denoiser.workers.heartbeat import read_heartbeat

    try:
        counters = await read_counters(runtime.redis_client())
    except Exception:
        counters = {"total": 0, "by_topic": {}}

    # Consumer liveness and lag, for the same reason: the consumer is another
    # pod with no HTTP surface, so this is the only place a scraper can see it.
    try:
        heartbeat = await read_heartbeat(runtime.redis_client())
    except Exception:
        heartbeat = None

    return metrics_response(
        dlq_counters=counters,
        consumer_heartbeat=heartbeat,
        include_consumer=True,
    )


@router.get("/telemetry/kernel-events")
def kernel_events(
    limit: int = Query(200, ge=1, le=MAX_PAGE_SIZE),
    since_ms: int | None = None,
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """Kernel events (TCP retransmits, OOM kills) captured by the eBPF collector.

    The collector was running and writing these to disk with no reader anywhere
    in the codebase. They now feed anomaly correlation, and this exposes them
    directly.
    """
    from denoiser.telemetry.ebpf_collector import EVENT_TYPES, read_events

    limit = max(1, min(int(limit), 2000))
    events = read_events(since_ms=since_ms, limit=limit)

    counts = {name: 0 for name in EVENT_TYPES.values()}
    for event in events:
        name = event.get("event_name")
        if name in counts:
            counts[name] += 1

    return {
        # Distinguish "tracing is off" from "tracing is on and the kernel is quiet".
        "tracing_supported": ebpf_agent.is_supported,
        "tracing_active": ebpf_agent.is_supported and getattr(ebpf_agent, "_running", False),
        "counts": counts,
        "events": events,
    }


# Host vitals and the metric stream moved to denoiser.api.routers_telemetry.
