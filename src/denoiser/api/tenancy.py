"""Which customer an identity belongs to, and how to remove a customer.

Two companies sharing one deployment only stay separate if every entry point
agrees on who a user works for. Local accounts inherit the tenant of the admin
who created them, but federated identities arrive from outside with nothing but
an email address — so SSO and SCIM route on the email's domain, which each
customer registers against their own tenant.

The deliberate asymmetry is in `resolve_identity_tenant`: a deployment where
nobody has registered a domain is a single-customer install, and falls back to
the one tenant that exists. The moment *any* tenant claims a domain, the
deployment is multi-customer and an unrecognised domain is rejected rather than
being dropped into whichever tenant sorts first.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from denoiser.logging import get_logger
from denoiser.storage.db import Tenant, utcnow
from denoiser.storage.secrets import decrypt, encrypt

logger = get_logger(__name__)


class TenantRoutingError(Exception):
    """An identity could not be attributed to exactly one customer."""


# ── Domains ──────────────────────────────────────────────────────────────────

def normalise_domains(domains: list[str] | None) -> list[str]:
    """Lowercase, strip a leading ``@`` or ``.``, drop blanks and duplicates."""
    seen: list[str] = []
    for raw in domains or []:
        domain = str(raw).strip().lower().lstrip("@").strip(".")
        if domain and domain not in seen:
            seen.append(domain)
    return seen


def domain_of(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower() or None


def tenant_claiming(db: Session, domain: str | None) -> Tenant | None:
    """The tenant that registered ``domain``, if any.

    Scanned in Python rather than pushed into SQL because `sso_domains` is a
    JSON array and the containment operators differ between Postgres and the
    SQLite used in tests. The tenant table is small — one row per customer.
    """
    if not domain:
        return None
    for tenant in db.query(Tenant).order_by(Tenant.id).all():
        if domain in normalise_domains(tenant.sso_domains):
            return tenant
    return None


def domain_routing_configured(db: Session) -> bool:
    """Whether any customer has claimed a domain — i.e. this is a shared install."""
    return any(normalise_domains(t.sso_domains) for t in db.query(Tenant).all())


def conflicting_domains(db: Session, domains: list[str], exclude_tenant_id: int | None = None) -> list[str]:
    """Domains already claimed by a *different* tenant.

    Two customers claiming the same domain would make routing ambiguous, and
    the loser would silently have their staff provisioned into the winner.
    """
    taken: list[str] = []
    for domain in normalise_domains(domains):
        owner = tenant_claiming(db, domain)
        if owner is not None and owner.id != exclude_tenant_id:
            taken.append(domain)
    return taken


def resolve_identity_tenant(db: Session, email: str | None) -> int | None:
    """The tenant a federated identity belongs to, by email domain.

    Raises `TenantRoutingError` when the deployment serves several customers and
    this address matches none of them — refusing the login is the only safe
    answer, since guessing puts one company's employee inside another's data.
    """
    domain = domain_of(email)
    tenant = tenant_claiming(db, domain)
    if tenant is not None:
        return tenant.id

    if domain_routing_configured(db):
        raise TenantRoutingError(
            f"No organisation is registered for the domain '{domain or email}'."
        )

    # Single-customer deployment: nobody has claimed a domain, so there is only
    # one place this user could belong.
    fallback = db.query(Tenant).order_by(Tenant.id).first()
    return fallback.id if fallback else None


# ── Per-tenant SCIM credentials ──────────────────────────────────────────────

def rotate_scim_token(db: Session, tenant: Tenant, new_token: str | None = None) -> str:
    """Issue this tenant's SCIM bearer token. Returned once, stored encrypted."""
    from denoiser.api.credentials import generate_api_key

    token = new_token or generate_api_key(prefix="scim")
    tenant.scim_token = encrypt(token)
    tenant.scim_token_rotated_at = utcnow()
    db.commit()
    db.refresh(tenant)
    logger.info("Rotated the SCIM token for tenant %s", tenant.id)
    return token


def tenant_for_scim_token(db: Session, presented: str | None) -> Tenant | None:
    """The tenant whose SCIM token matches, compared in constant time."""
    from denoiser.api.credentials import secrets_match

    if not presented:
        return None
    for tenant in db.query(Tenant).order_by(Tenant.id).all():
        if not tenant.scim_token:
            continue
        if secrets_match(presented, decrypt(tenant.scim_token)):
            return tenant
    return None


def describe_scim_token(tenant: Tenant | None) -> dict:
    """Rotation state, without revealing the token itself."""
    rotated: datetime | None = tenant.scim_token_rotated_at if tenant else None
    return {
        "configured": bool(tenant and tenant.scim_token),
        "last_rotated_at": rotated.isoformat() + "Z" if rotated else None,
    }


# ── Offboarding ──────────────────────────────────────────────────────────────

#: Every table carrying a `tenant_id`. Kept as an explicit list rather than
#: discovered by reflection so that adding a tenant-scoped table without
#: deciding what offboarding should do with it fails the test that compares
#: this list against the mapped models.
TENANT_SCOPED_MODELS = (
    "Incident", "Team", "AnalysisRun", "User", "AuditLog", "AlertLog", "Webhook",
    "Span", "ServiceLevelObjective", "Dashboard", "MetricRule", "ExtractedMetric",
    "Runbook", "Monitor", "Notebook", "BillingMeter", "Integration",
    "DeploymentMarker", "LogIssue", "IssueComment", "IssueEvent", "SavedQuery",
    # A departing customer's IdP configuration must go with them: it holds
    # their client secret and their IdP certificate, and leaving the SAML
    # issuer registered would keep routing assertions to a tenant that no
    # longer exists.
    "TenantIdentityProvider",
    # The commercial record for this workspace. A departing customer's
    # subscription must go with them: leaving it behind means a re-created
    # workspace with the same id inherits somebody else's plan, and it holds the
    # provider customer id, which links the row to a real person's payment
    # method.
    "Subscription",
)

#: Tables that belong to a customer only through a parent row. They carry no
#: `tenant_id` of their own, so deleting by tenant would miss them entirely and
#: leave a departed customer's saved queries and SLO history behind. Each entry
#: is (child model, child FK attribute, parent model, parent key attribute), and
#: they are deleted *before* their parents, while the parents are still findable.
CHILD_MODELS = (
    # SavedQuery used to be here: it carried no tenant_id of its own and was
    # reachable only through its author. It carries one now, so it is deleted
    # with the rest of the tenant-scoped tables.
    ("SLODataPoint", "slo_id", "ServiceLevelObjective", "id"),
    ("RunbookExecution", "runbook_id", "Runbook", "id"),
)

#: Deliberately deployment-wide, not owned by any one customer: the tenant
#: registry itself, operator settings, and the token denylist (which must
#: outlive the user whose token it revoked).
#: Deployment-wide, deliberately untouched by a purge.
#:
#: `ErasureRecord` is here for a specific reason: it is the evidence that a
#: customer's data was deleted, so purging it along with the customer would
#: destroy the only proof of the erasure it describes. It holds no customer
#: data — an id, a name, timestamps and ClickHouse mutation ids — which is the
#: minimum needed to answer a regulator asking whether the deletion completed.
#: `Plan` is the price list — the vendor's own catalogue, not any customer's
#: data. `ProcessedWebhookEvent` is the idempotency ledger; purging a customer's
#: entries would let a redelivered event from before their departure be applied
#: again, and it holds only a provider event id and a type.
GLOBAL_MODELS = (
    "Tenant", "PlatformSetting", "RevokedToken", "ErasureRecord",
    "Plan", "ProcessedWebhookEvent",
)


def purge_tenant(db: Session, tenant: Tenant) -> dict:
    """Delete a customer and everything of theirs, across every store.

    Returns a per-store report instead of raising, because the stores fail
    independently: if ClickHouse is unreachable we still want the relational
    rows gone, and we want the caller told exactly what was left behind rather
    than a bare 500 that leaves them guessing.

    The tenant row itself is removed last, and only if the relational delete
    succeeded — while it exists the purge can be retried, and an operator can
    see that the customer is mid-offboarding.
    """
    from denoiser.storage import db as models

    tenant_id = tenant.id
    report: dict = {"tenant_id": tenant_id, "name": tenant.name, "deleted": {}, "errors": []}

    # 1. Relational rows, in one transaction: a half-deleted customer whose
    #    users are gone but whose incidents remain is worse than none deleted.
    try:
        # Children first, while their parents are still there to identify them.
        for child_name, fk_attr, parent_name, parent_key in CHILD_MODELS:
            child = getattr(models, child_name, None)
            parent = getattr(models, parent_name, None)
            if child is None or parent is None:
                continue
            parent_ids = [
                row[0] for row in
                db.query(getattr(parent, parent_key)).filter(parent.tenant_id == tenant_id).all()
            ]
            if not parent_ids:
                continue
            removed = (
                db.query(child)
                .filter(getattr(child, fk_attr).in_(parent_ids))
                .delete(synchronize_session=False)
            )
            if removed:
                report["deleted"][child.__tablename__] = removed

        for model_name in TENANT_SCOPED_MODELS:
            model = getattr(models, model_name, None)
            if model is None:
                continue
            removed = (
                db.query(model)
                .filter(model.tenant_id == tenant_id)
                .delete(synchronize_session=False)
            )
            if removed:
                report["deleted"][model.__tablename__] = removed
        db.commit()
    except Exception as e:
        db.rollback()
        report["errors"].append(f"database: {e}")
        logger.error("Tenant %s purge failed at the database step: %s", tenant_id, e)
        return report

    # 2. Log rows in ClickHouse.
    #
    #    Submitted, not completed. ClickHouse `ALTER … DELETE` is asynchronous,
    #    so this step returning success means the mutations are queued — on a
    #    large table the parts are still being rewritten for some time after.
    #    The mutation ids are carried out with the report so the erasure can be
    #    certified later against `system.mutations`, rather than against this
    #    function having returned.
    try:
        from denoiser import runtime

        store = runtime.clickhouse_store()
        submission = store.submit_tenant_deletion(str(tenant_id))
        report["deleted"]["clickhouse"] = submission.get("submitted", False)
        report["clickhouse_mutations"] = [
            m["mutation_id"] for m in submission.get("mutations", [])
        ]
        if submission.get("error"):
            report["errors"].append(f"clickhouse: {submission['error']}")
        if submission.get("mutation_lookup_error"):
            # The delete went in; what is missing is the ability to confirm it.
            # Recorded distinctly so it is not read as the purge having failed.
            report["warnings"] = report.get("warnings", []) + [
                "clickhouse: deletion submitted but its mutation ids could not be "
                f"recorded ({submission['mutation_lookup_error']}); completion "
                "cannot be verified automatically"
            ]
    except Exception as e:
        report["errors"].append(f"clickhouse: {e}")
        logger.error("Tenant %s purge failed at the ClickHouse step: %s", tenant_id, e)

    # 3. Embeddings.
    try:
        from denoiser.storage.vector_store import VectorStore

        report["deleted"]["embeddings"] = VectorStore().delete_tenant(tenant_id)
    except Exception as e:
        report["errors"].append(f"vector_store: {e}")
        logger.error("Tenant %s purge failed at the vector store step: %s", tenant_id, e)

    # 4. Uploaded source files.
    try:
        import shutil

        from denoiser.api.sources import tenant_dir

        directory = tenant_dir(tenant_id)
        files = sum(1 for p in directory.rglob("*") if p.is_file())
        shutil.rmtree(directory, ignore_errors=True)
        report["deleted"]["source_files"] = files

        # Archives that were written locally but never uploaded, or uploaded and
        # kept. Named `<kind>_t<tenant>_<epoch>.jsonl.gz` by the archiver.
        from denoiser.storage.archiver import ARCHIVE_DIR

        local_archives = 0
        for path in ARCHIVE_DIR.glob(f"*_t{tenant_id}_*.jsonl.gz"):
            path.unlink(missing_ok=True)
            local_archives += 1
        report["deleted"]["local_archives"] = local_archives
    except Exception as e:
        report["errors"].append(f"source_files: {e}")
        logger.error("Tenant %s purge failed at the source-file step: %s", tenant_id, e)

    # 5. Cold archives in object storage. Reachable only because archives are
    #    partitioned by tenant; a single commingled file per run could not be
    #    deleted for one customer without destroying the others'.
    try:
        from denoiser.storage.object_store import ObjectStore

        store = ObjectStore()
        removed = 0
        for kind in ("logs", "traces"):
            for key in store.list_objects(prefix=f"archive/{kind}/tenant={tenant_id}/"):
                if store.delete_object(key):
                    removed += 1
        report["deleted"]["archives"] = removed
    except Exception as e:
        report["errors"].append(f"object_store: {e}")
        logger.error("Tenant %s purge failed at the object-store step: %s", tenant_id, e)

    # 6. Finally the tenant itself, so a failure above leaves something to retry.
    try:
        db.delete(tenant)
        db.commit()
        report["deleted"]["tenant"] = 1
    except Exception as e:
        db.rollback()
        report["errors"].append(f"tenant_row: {e}")

    logger.warning(
        "Purged tenant %s (%s): %s, errors=%s",
        tenant_id, report["name"], report["deleted"], report["errors"],
    )
    return report
