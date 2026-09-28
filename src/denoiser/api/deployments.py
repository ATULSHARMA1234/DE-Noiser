from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from denoiser.api.auth import User, require_role
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.storage.db import DeploymentMarker as DBDeploymentMarker

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

    model_config = ConfigDict(from_attributes=True)

@router.get("", response_model=list[DeploymentMarkerSchema])
def list_deployments(
    scope: TenantScope = Depends(tenant_scope),
    _: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    return (
        scope.query(DBDeploymentMarker)
        .order_by(DBDeploymentMarker.timestamp.desc())
        .limit(100)
        .all()
    )

@router.post("", response_model=DeploymentMarkerSchema)
def create_deployment(
    payload: DeploymentMarkerCreateSchema,
    scope: TenantScope = Depends(tenant_scope),
    _: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    deployment = scope.add(DBDeploymentMarker(
        service=payload.service,
        version=payload.version,
        environment=payload.environment,
        description=payload.description,
    ))
    scope.db.commit()
    scope.db.refresh(deployment)
    return deployment
