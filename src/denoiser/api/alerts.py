
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from denoiser.api.auth import require_role
from denoiser.storage.db import AlertLog, User, get_db

router = APIRouter(prefix="/alerts", tags=["Alerts"])

@router.get("/")
def get_alert_history(
    limit: int = 100,
    skip: int = 0,
    priority: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))
):
    """Fetch chronological log of sent notifications."""
    query = db.query(AlertLog)

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
