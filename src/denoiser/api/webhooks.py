"""Alert-destination CRUD, delivery test, and delivery history."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from denoiser.api.auth import require_role
from denoiser.api.pagination import MAX_PAGE_SIZE
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.integrations.alert_router import AlertPayload, ChannelType, alert_router
from denoiser.storage.db import User, get_db

router = APIRouter(tags=["webhooks"])

# ─── WEBHOOKS — Alert Routing CRUD (Task 15) ─────────────────────────────────

class WebhookCreateRequest(BaseModel):
    name: str
    channel_type: str
    url: str
    min_priority: str = "P1"
    enabled: bool = True
    extra: dict = {}


class WebhookUpdateRequest(BaseModel):
    name: str | None = None
    url: str | None = None
    min_priority: str | None = None
    enabled: bool | None = None
    extra: dict | None = None


@router.get("/webhooks")
def list_webhooks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """List this tenant's alert destinations, with credentials masked."""
    from denoiser.integrations import webhook_store

    return [
        webhook_store.to_public_dict(row)
        for row in webhook_store.list_webhooks(db, current_user.tenant_id)
    ]


@router.post("/webhooks", status_code=201)
def create_webhook(
    body: WebhookCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Register a new alert destination for the caller's tenant."""
    from denoiser.integrations import webhook_store
    from denoiser.integrations.net_guard import DestinationNotAllowed, validate_destination

    try:
        channel = ChannelType(body.channel_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid channel_type '{body.channel_type}'. Valid: slack, pagerduty, teams, generic")

    # Rejected here as well as at delivery so the operator finds out while they
    # are still looking at the form, not silently three alerts later.
    try:
        validate_destination(body.url)
    except DestinationNotAllowed as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    row = webhook_store.create_webhook(
        db,
        current_user.tenant_id,
        name=body.name,
        channel_type=channel.value,
        url=body.url,
        min_priority=body.min_priority,
        enabled=body.enabled,
        extra=body.extra,
    )
    return {"status": "registered", **webhook_store.to_public_dict(row)}


@router.put("/webhooks/{webhook_id}")
def update_webhook(
    webhook_id: str,
    body: WebhookUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Update a webhook belonging to the caller's tenant."""
    from denoiser.integrations import webhook_store
    from denoiser.integrations.net_guard import DestinationNotAllowed, validate_destination

    row = webhook_store.get_webhook(db, current_user.tenant_id, webhook_id)
    if not row:
        raise HTTPException(status_code=404, detail="Webhook not found")

    if body.url is not None:
        try:
            validate_destination(body.url)
        except DestinationNotAllowed as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    row = webhook_store.update_webhook(
        db, row,
        name=body.name,
        url=body.url,
        min_priority=body.min_priority,
        enabled=body.enabled,
        extra=body.extra,
    )
    return {"status": "updated", **webhook_store.to_public_dict(row)}


@router.delete("/webhooks/{webhook_id}")
def delete_webhook(
    webhook_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Remove an alert destination belonging to the caller's tenant."""
    from denoiser.integrations import webhook_store

    row = webhook_store.get_webhook(db, current_user.tenant_id, webhook_id)
    if not row:
        raise HTTPException(status_code=404, detail="Webhook not found")
    webhook_store.delete_webhook(db, row)
    return {"status": "deleted", "id": webhook_id}


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Fire a synthetic P1 test alert to one of the caller's own destinations."""
    from denoiser.integrations import webhook_store

    row = webhook_store.get_webhook(db, current_user.tenant_id, webhook_id)
    if not row:
        raise HTTPException(status_code=404, detail="Webhook not found")
    cfg = webhook_store.to_config(row)
    if cfg is None:
        raise HTTPException(
            status_code=409,
            detail="This destination's stored URL could not be decrypted; re-save it to continue.",
        )

    test_alert = AlertPayload(
        source="semanticos/test",
        run_id="test_run",
        priority="P1",
        cluster_id=0,
        cluster_summary="[TEST] SemanticOS webhook connectivity verification",
        representative_log="INFO [test] Alert routing system connectivity test - all channels operational",
        anomaly_score=0.72,
        causal_links=[],
        intelligence={
            "failure_domain": "Test Channel",
            "incident_summary": "This is a test alert from SemanticOS to verify webhook connectivity.",
            "root_cause_hints": ["No action required — this is a connectivity test."]
        },
        keyword_flag=False,
    )
    records = await alert_router._deliver_with_retry(cfg, test_alert)
    return {
        "status": records.status.value,
        "http_status": records.http_status,
        "latency_ms": records.latency_ms,
        "error": records.error,
    }


@router.get("/webhooks/log")
def get_delivery_log(
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
    scope: TenantScope = Depends(tenant_scope),
):
    """Recent alert-delivery records for the caller's tenant.

    Read from the database rather than the router's in-process list: the list
    was global, so every tenant saw every other tenant's delivery history, and
    it was empty after a restart.
    """
    from denoiser.storage.db import AlertLog

    rows = (
        scope.query(AlertLog)
        .order_by(AlertLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "webhook_id": r.webhook_id,
            "alert_fingerprint": r.alert_fingerprint,
            "priority": r.priority,
            "status": r.status,
            "http_status": r.http_status,
            "latency_ms": r.latency_ms,
            "error": r.error,
            "timestamp": r.timestamp,
        }
        for r in rows
    ]


