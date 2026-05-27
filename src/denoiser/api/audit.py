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
        # Process the request
        response: Response = await call_next(request)

        # Only log mutating actions
        if request.method in ["POST", "PUT", "DELETE"]:
            # We don't have easy access to `current_user` in Starlette middleware
            # without parsing the JWT again. We'll try to extract the JWT token directly.
            user_id = None
            try:
                auth_header = request.headers.get("Authorization")
                if auth_header and auth_header.startswith("Bearer "):
                    token = auth_header.split(" ")[1]
                    from jose import jwt

                    from denoiser.api.auth import ALGORITHM, SECRET_KEY

                    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                    email = payload.get("sub")
                    if email:
                        db = SessionLocal()
                        user = db.query(User).filter(User.email == email).first()
                        if user:
                            user_id = user.id
                        db.close()
            except Exception:
                pass  # If decoding fails, user_id remains None

            ip_address = request.client.host if request.client else None

            try:
                db = SessionLocal()
                audit_log = AuditLog(
                    user_id=user_id,
                    action=request.method,
                    resource_type=request.url.path,
                    resource_id=None,
                    details={"status_code": response.status_code},
                    ip_address=ip_address,
                    timestamp=datetime.now(UTC)
                )
                db.add(audit_log)
                db.commit()
                db.close()
            except Exception as e:
                logger.error(f"Failed to write audit log: {e}")

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
