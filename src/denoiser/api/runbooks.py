from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
from denoiser.storage.db import get_db, Runbook, RunbookExecution
from denoiser.api.auth import get_current_user, require_role, User

router = APIRouter(prefix="/runbooks", tags=["runbooks"])

class StepSchema(BaseModel):
    name: str
    action: str
    url: Optional[str] = None
    service: Optional[str] = None

class TriggerConditionSchema(BaseModel):
    keyword: Optional[str] = None

class RunbookCreateSchema(BaseModel):
    name: str
    trigger_condition: Dict[str, Any]
    steps: List[Dict[str, Any]]
    enabled: bool = True

class RunbookResponseSchema(BaseModel):
    id: int
    name: str
    trigger_condition: Dict[str, Any]
    steps: List[Dict[str, Any]]
    enabled: bool

    class Config:
        from_attributes = True

class RunbookExecutionResponseSchema(BaseModel):
    id: int
    runbook_id: int
    incident_id: Optional[int]
    status: str
    logs: List[str]
    created_at: str

    class Config:
        from_attributes = True


@router.get("", response_model=List[RunbookResponseSchema])
def list_runbooks(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    runbooks = db.query(Runbook).filter(Runbook.tenant_id == current_user.tenant_id).order_by(Runbook.created_at.desc()).all()
    return runbooks

@router.post("", response_model=RunbookResponseSchema)
def create_runbook(payload: RunbookCreateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    rb = Runbook(
        name=payload.name,
        tenant_id=current_user.tenant_id,
        trigger_condition=payload.trigger_condition,
        steps=payload.steps,
        enabled=payload.enabled
    )
    db.add(rb)
    db.commit()
    db.refresh(rb)
    return rb

@router.delete("/{runbook_id}")
def delete_runbook(runbook_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    rb = db.query(Runbook).filter(Runbook.id == runbook_id, Runbook.tenant_id == current_user.tenant_id).first()
    if not rb:
        raise HTTPException(status_code=404, detail="Runbook not found")
    db.delete(rb)
    db.commit()
    return {"status": "deleted"}

@router.get("/executions", response_model=List[dict])
def list_executions(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    executions = db.query(RunbookExecution).join(Runbook).filter(Runbook.tenant_id == current_user.tenant_id).order_by(RunbookExecution.created_at.desc()).limit(100).all()
    
    return [
        {
            "id": ex.id,
            "runbook_id": ex.runbook_id,
            "incident_id": ex.incident_id,
            "status": ex.status,
            "logs": ex.logs,
            "created_at": ex.created_at.isoformat()
        } for ex in executions
    ]
