import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from denoiser.api.auth import require_role
from denoiser.api.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.logging import get_logger
from denoiser.storage.db import AuditLog, SessionLocal, User

logger = get_logger(__name__)

router = APIRouter(prefix="/audit", tags=["Audit"])


# Read paths that touch tenant log content or its derived findings. Auditing
# only mutations answers "who changed this" but not "who read this" — and for a
# platform holding log lines that contain personal data, read access is exactly
# what SOC 2 CC7.2 and HIPAA §164.312(b) require a record of.
#
# Deliberately not every GET: listing dashboards or fetching /health generates
# noise that buries the accesses an investigator is actually looking for.
_AUDITED_READ_PATTERNS = [
    re.compile(r"^/v1/logs/query$"),
    re.compile(r"^/query$"),
    re.compile(r"^/query/histogram$"),
    re.compile(r"^/runs/[^/]+$"),
    re.compile(r"^/analysis/runs/[^/]+$"),
    re.compile(r"^/issues/\d+$"),
    re.compile(r"^/incidents/\d+$"),
    re.compile(r"^/traces/[^/]+$"),
    re.compile(r"^/sources$"),
]


def _is_audited_read(path: str) -> bool:
    return any(p.match(path) for p in _AUDITED_READ_PATTERNS)


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        mutating = request.method in ("POST", "PUT", "DELETE", "PATCH")
        audited_read = request.method == "GET" and _is_audited_read(request.url.path)
        if not mutating and not audited_read:
            return response

        # Identity is resolved once by the get_current_user dependency, which
        # stamps request.state during handling — no JWT re-decode, no extra user
        # lookup, and it honours revocation/deactivation (a rejected request
        # never sets state and falls back to the system-audit actor).
        user_id = getattr(request.state, "audit_user_id", None)
        tenant_id = getattr(request.state, "audit_tenant_id", None)
        ip_address = request.client.host if request.client else None

        # Handlers record what actually changed by stamping request.state; the
        # middleware cannot see it, since by the time it runs the transaction is
        # already committed and the previous values are gone.
        changes = getattr(request.state, "audit_changes", None)

        details: dict = {"status_code": response.status_code}
        if audited_read:
            details["access"] = "read"
        if changes:
            details["changes"] = changes

        db = SessionLocal()
        try:
            if user_id is None:
                sys_user = db.query(User).filter(
                    User.email == "system-audit@semanticos.io"
                ).first()
                user_id = sys_user.id if sys_user else None

            db.add(AuditLog(
                tenant_id=tenant_id,
                user_id=user_id,
                action=request.method,
                resource_type=request.url.path,
                resource_id=None,
                details=details,
                ip_address=ip_address,
                timestamp=datetime.now(UTC),
            ))
            db.commit()
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
        finally:
            db.close()

        return response


def record_changes(request: Request | None, changes: dict) -> None:
    """Attach a before/after diff to the audit row for this request.

    Called by handlers that mutate configuration. Without it the audit trail
    records that a PUT to /settings returned 200, which cannot answer the
    question an auditor actually asks: what was the retention period before
    someone reduced it, and to what?
    """
    if request is None or not changes:
        return
    existing = getattr(request.state, "audit_changes", None) or {}
    existing.update(changes)
    request.state.audit_changes = existing


def diff_fields(before: dict, after: dict) -> dict:
    """``{field: {"from": old, "to": new}}`` for the values that actually moved."""
    changed = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value != new_value:
            changed[key] = {"from": old_value, "to": new_value}
    return changed


@router.get("/")
def get_audit_logs(
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    skip: int = Query(0, ge=0),
    action: str | None = None,
    user_id: int | None = None,
    scope: TenantScope = Depends(tenant_scope),
    _: User = Depends(require_role(["ADMIN"])),
):
    """Audit records for the caller's tenant.

    Tenant-filtered: without it, one customer's admin could read every other
    customer's action history, user ids and source IPs — including the timing of
    their credential rotations.
    """
    query = scope.query(AuditLog)

    if action:
        query = query.filter(AuditLog.action == action)
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)

    logs = query.order_by(AuditLog.timestamp.desc()).offset(skip).limit(limit).all()

    return {
        "status": "success",
        "total": query.count(),
        "data": [
            {
                "id": log.id,
                "user_id": log.user_id,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "details": log.details,
                "ip_address": log.ip_address,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None
            }
            for log in logs
        ]
    }
