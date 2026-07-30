
from fastapi import APIRouter, Depends

from denoiser.api.auth import require_role
from denoiser.api.pagination import limit_param, offset_param
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.storage.db import AlertLog, User

router = APIRouter(prefix="/alerts", tags=["Alerts"])

@router.get("/")
def get_alert_history(
    limit: int = limit_param(),
    skip: int = offset_param(),
    priority: str | None = None,
    status: str | None = None,
    scope: TenantScope = Depends(tenant_scope),
    _: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """Chronological log of notifications sent for the caller's tenant."""
    # Unfiltered, this served every tenant's notification history — including
    # which of their channels failed and when — to whoever asked.
    query = scope.query(AlertLog)

    if priority:
        query = query.filter(AlertLog.priority == priority)
    if status:
        query = query.filter(AlertLog.status == status)

    logs = query.order_by(AlertLog.timestamp.desc()).offset(skip).limit(limit).all()

    return {
        "status": "success",
        "total": query.count(),
        "data": [
            {
                "id": log.id,
                "webhook_id": log.webhook_id,
                "alert_fingerprint": log.alert_fingerprint,
                "priority": log.priority,
                "status": log.status,
                "http_status": log.http_status,
                "latency_ms": log.latency_ms,
                "error": log.error,
                "timestamp": log.timestamp
            }
            for log in logs
        ]
    }
