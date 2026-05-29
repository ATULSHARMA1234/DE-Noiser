from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from denoiser.storage.db import get_db, Runbook as DBRunbook, RunbookExecution as DBRunbookExecution
from denoiser.api.auth import require_role, User
from denoiser.automation.models import RunbookCreateSchema, RunbookSchema, RunbookExecutionSchema

router = APIRouter(prefix="/runbooks", tags=["automation"])

@router.get("", response_model=List[RunbookSchema])
def list_runbooks(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    runbooks = db.query(DBRunbook).order_by(DBRunbook.created_at.desc()).all()
    return runbooks

@router.post("", response_model=RunbookSchema)
def create_runbook(payload: RunbookCreateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    runbook = DBRunbook(
        name=payload.name,
        trigger_condition=payload.trigger_condition,
        steps=payload.steps,
        enabled=payload.enabled
    )
    db.add(runbook)
    db.commit()
    db.refresh(runbook)
    return runbook

@router.delete("/{runbook_id}")
def delete_runbook(runbook_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    runbook = db.query(DBRunbook).filter(DBRunbook.id == runbook_id).first()
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found")
        
    db.delete(runbook)
    db.commit()
    return {"status": "deleted"}

@router.get("/executions", response_model=List[RunbookExecutionSchema])
def list_executions(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    executions = db.query(DBRunbookExecution).order_by(DBRunbookExecution.created_at.desc()).limit(100).all()
    return executions
