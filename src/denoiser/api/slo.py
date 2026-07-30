
import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.api.pagination import ResourceId
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.slo.engine import calculate_slo_status
from denoiser.slo.models import SLOCreateSchema, SLOSchema, SLOStatusSchema
from denoiser.storage.db import AlertLog, ServiceLevelObjective, get_db
from denoiser.utils.time import utcnow

router = APIRouter(prefix="/slos", tags=["slo"])

@router.get("", response_model=list[SLOSchema])
def list_slos(scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    return scope.query(ServiceLevelObjective).order_by(ServiceLevelObjective.created_at.desc()).all()

@router.post("", response_model=SLOSchema)
def create_slo(payload: SLOCreateSchema, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    slo = ServiceLevelObjective(
        name=payload.name,
        service=payload.service,
        sli_type=payload.sli_type,
        target_percentage=payload.target_percentage,
        window_days=payload.window_days,
        latency_threshold_ms=payload.latency_threshold_ms,
    )
    scope.add(slo)
    db.commit()
    db.refresh(slo)
    return slo

@router.delete("/{slo_id}")
def delete_slo(slo_id: ResourceId, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["ADMIN"]))):
    slo = scope.get_or_404(ServiceLevelObjective, slo_id, "SLO not found")
    db.delete(slo)
    db.commit()
    return {"status": "deleted"}

@router.get("/{slo_id}/status", response_model=SLOStatusSchema)
def get_slo_status(slo_id: ResourceId, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    slo = scope.get_or_404(ServiceLevelObjective, slo_id, "SLO not found")

    # calculate_slo_status returns a plain dict, not the response model — reading
    # it with attribute access raised AttributeError on every call, so the SLO
    # page could never load a status at all.
    status = calculate_slo_status(db, slo)

    # Auto-alert on budget breach (Google SRE pattern)
    if status["status"] == "BREACHED":
        fingerprint = f"slo_breach_{slo.id}"
        # Only create if no recent alert for this SLO (within 1 hour)
        one_hour_ago = (utcnow() - datetime.timedelta(hours=1)).isoformat()
        existing = db.query(AlertLog).filter(
            AlertLog.alert_fingerprint == fingerprint,
            AlertLog.timestamp > one_hour_ago
        ).first()
        if not existing:
            alert = AlertLog(
                webhook_id="slo_engine",
                alert_fingerprint=fingerprint,
                priority="critical",
                status="fired",
                error=f"SLO '{slo.name}' on {slo.service} has BREACHED — error budget exhausted (burn rate: {status['burn_rate']:.1f}x)",
            )
            db.add(alert)
            db.commit()

    return status
