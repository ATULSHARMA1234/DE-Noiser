from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from denoiser.storage.db import get_db, DeploymentMarker as DBDeploymentMarker
from denoiser.api.auth import require_role, User

router = APIRouter(prefix="/deployments", tags=["deployments"])

class DeploymentMarkerCreateSchema(BaseModel):
    service: str
    version: str
    environment: str
    description: Optional[str] = None

class DeploymentMarkerSchema(BaseModel):
    id: int
    service: str
    version: str
    environment: str
    description: Optional[str]
    timestamp: datetime

    class Config:
        from_attributes = True

@router.get("", response_model=List[DeploymentMarkerSchema])
def list_deployments(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    deployments = db.query(DBDeploymentMarker).filter(DBDeploymentMarker.tenant_id == current_user.tenant_id).order_by(DBDeploymentMarker.timestamp.desc()).limit(100).all()
    return deployments

@router.post("", response_model=DeploymentMarkerSchema)
def create_deployment(payload: DeploymentMarkerCreateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    deployment = DBDeploymentMarker(
        tenant_id=current_user.tenant_id,
        service=payload.service,
        version=payload.version,
        environment=payload.environment,
        description=payload.description
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    return deployment
