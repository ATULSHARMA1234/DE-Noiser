from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from denoiser.api.auth import require_role
from denoiser.logging import get_logger
from denoiser.storage.db import AuditLog, SessionLocal, User, get_db

logger = get_logger(__name__)

router = APIRouter(prefix="/audit", tags=["Audit"])


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        # Only mutating actions are audited.
        if request.method not in ("POST", "PUT", "DELETE"):
            return response

        # Identity is resolved once by the get_current_user dependency, which
        # stamps request.state during handling — no JWT re-decode, no extra user
        # lookup, and it honours revocation/deactivation (a rejected request
        # never sets state and falls back to the system-audit actor).
        user_id = getattr(request.state, "audit_user_id", None)
        ip_address = request.client.host if request.client else None

        db = SessionLocal()
        try:
            if user_id is None:
                sys_user = db.query(User).filter(
                    User.email == "system-audit@semanticos.io"
                ).first()
                user_id = sys_user.id if sys_user else None

            db.add(AuditLog(
                user_id=user_id,
                action=request.method,
                resource_type=request.url.path,
                resource_id=None,
                details={"status_code": response.status_code},
                ip_address=ip_address,
                timestamp=datetime.now(UTC),
            ))
            db.commit()
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
        finally:
            db.close()

        return response


@router.get("/")
def get_audit_logs(
    limit: int = 100,
    skip: int = 0,
    action: str | None = None,
    user_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"]))
):
    """Admin endpoint to fetch audit logs."""
    query = db.query(AuditLog)

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
