from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.storage.db import DeploymentMarker as DBDeploymentMarker
from denoiser.storage.db import get_db

router = APIRouter(prefix="/deployments", tags=["deployments"])

class DeploymentMarkerCreateSchema(BaseModel):
    service: str
    version: str
    environment: str
    description: str | None = None

class DeploymentMarkerSchema(BaseModel):
    id: int
    service: str
    version: str
    environment: str
    description: str | None
    timestamp: datetime

    class Config:
        from_attributes = True

@router.get("", response_model=list[DeploymentMarkerSchema])
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
