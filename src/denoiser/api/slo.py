
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.slo.engine import calculate_slo_status
from denoiser.slo.models import SLOCreateSchema, SLOSchema, SLOStatusSchema
from denoiser.storage.db import ServiceLevelObjective, get_db

router = APIRouter(prefix="/slos", tags=["slo"])

@router.get("", response_model=list[SLOSchema])
def list_slos(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    slos = db.query(ServiceLevelObjective).order_by(ServiceLevelObjective.created_at.desc()).all()
    return slos

@router.post("", response_model=SLOSchema)
def create_slo(payload: SLOCreateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    slo = ServiceLevelObjective(
        name=payload.name,
        service=payload.service,
        sli_type=payload.sli_type,
        target_percentage=payload.target_percentage,
        window_days=payload.window_days
    )
    db.add(slo)
    db.commit()
    db.refresh(slo)
    return slo

@router.delete("/{slo_id}")
def delete_slo(slo_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    slo = db.query(ServiceLevelObjective).filter(ServiceLevelObjective.id == slo_id).first()
    if not slo:
        raise HTTPException(status_code=404, detail="SLO not found")
    db.delete(slo)
    db.commit()
    return {"status": "deleted"}

@router.get("/{slo_id}/status", response_model=SLOStatusSchema)
def get_slo_status(slo_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    slo = db.query(ServiceLevelObjective).filter(ServiceLevelObjective.id == slo_id).first()
    if not slo:
        raise HTTPException(status_code=404, detail="SLO not found")

    return calculate_slo_status(db, slo)
