from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from denoiser.storage.db import Monitor, get_db
from denoiser.api.auth import User, require_role

router = APIRouter(prefix="/monitors", tags=["monitors"])

class MonitorCreateSchema(BaseModel):
    name: str
    type: str = "log alert"
    query: str
    message: Optional[str] = None
    severity: str = "warning"
    threshold_critical: Optional[float] = None
    threshold_warning: Optional[float] = None
    enabled: bool = True

class MonitorUpdateSchema(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    query: Optional[str] = None
    message: Optional[str] = None
    severity: Optional[str] = None
    threshold_critical: Optional[float] = None
    threshold_warning: Optional[float] = None
    enabled: Optional[bool] = None

@router.get("", response_model=list[dict[str, Any]])
def list_monitors(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    query = db.query(Monitor)
    if current_user.tenant_id:
        query = query.filter(Monitor.tenant_id == current_user.tenant_id)
    monitors = query.all()
    return [
        {
            "id": m.id,
            "name": m.name,
            "type": m.type,
            "query": m.query,
            "message": m.message,
            "severity": m.severity,
            "threshold_critical": m.threshold_critical,
            "threshold_warning": m.threshold_warning,
            "enabled": m.enabled,
            "created_at": m.created_at.isoformat() if m.created_at else None
        }
        for m in monitors
    ]

@router.post("", response_model=dict[str, Any])
def create_monitor(payload: MonitorCreateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    m = Monitor(
        tenant_id=current_user.tenant_id,
        name=payload.name,
        type=payload.type,
        query=payload.query,
        message=payload.message,
        severity=payload.severity,
        threshold_critical=payload.threshold_critical,
        threshold_warning=payload.threshold_warning,
        enabled=payload.enabled
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"status": "created", "id": m.id}

@router.get("/{monitor_id}", response_model=dict[str, Any])
def get_monitor(monitor_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    m = db.query(Monitor).filter(Monitor.id == monitor_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Monitor not found")
    if current_user.tenant_id and m.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return {
        "id": m.id,
        "name": m.name,
        "type": m.type,
        "query": m.query,
        "message": m.message,
        "severity": m.severity,
        "threshold_critical": m.threshold_critical,
        "threshold_warning": m.threshold_warning,
        "enabled": m.enabled,
        "created_at": m.created_at.isoformat() if m.created_at else None
    }

@router.put("/{monitor_id}", response_model=dict[str, Any])
def update_monitor(monitor_id: int, payload: MonitorUpdateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    m = db.query(Monitor).filter(Monitor.id == monitor_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Monitor not found")
    if current_user.tenant_id and m.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(m, key, value)
        
    db.commit()
    return {"status": "updated", "id": m.id}

@router.delete("/{monitor_id}", response_model=dict[str, Any])
def delete_monitor(monitor_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    m = db.query(Monitor).filter(Monitor.id == monitor_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Monitor not found")
    if current_user.tenant_id and m.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    db.delete(m)
    db.commit()
    return {"status": "deleted", "id": m.id}
