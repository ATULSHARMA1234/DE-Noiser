from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.storage.db import Runbook, RunbookExecution, get_db

router = APIRouter(prefix="/runbooks", tags=["runbooks"])

class StepSchema(BaseModel):
    name: str
    action: str
    url: str | None = None
    service: str | None = None

class TriggerConditionSchema(BaseModel):
    keyword: str | None = None

class RunbookCreateSchema(BaseModel):
    name: str
    trigger_condition: dict[str, Any]
    steps: list[dict[str, Any]]
    enabled: bool = True

class RunbookResponseSchema(BaseModel):
    id: int
    name: str
    trigger_condition: dict[str, Any]
    steps: list[dict[str, Any]]
    enabled: bool

    class Config:
        from_attributes = True

class RunbookExecutionResponseSchema(BaseModel):
    id: int
    runbook_id: int
    incident_id: int | None
    status: str
    logs: list[str]
    created_at: str

    class Config:
        from_attributes = True


@router.get("", response_model=list[RunbookResponseSchema])
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

@router.get("/executions", response_model=list[dict])
def list_executions(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    executions = (
        db.query(RunbookExecution)
        .select_from(RunbookExecution)
        .join(Runbook, Runbook.id == RunbookExecution.runbook_id)
        .filter(Runbook.tenant_id == current_user.tenant_id)
        .order_by(RunbookExecution.created_at.desc())
        .limit(100)
        .all()
    )

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
