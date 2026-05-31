from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.storage.db import Integration as DBIntegration
from denoiser.storage.db import get_db

router = APIRouter(prefix="/integrations", tags=["integrations"])

class IntegrationCreateSchema(BaseModel):
    provider: str
    name: str
    config: dict[str, Any]

class IntegrationSchema(BaseModel):
    id: int
    provider: str
    name: str
    config: dict[str, Any]
    enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True

@router.get("", response_model=list[IntegrationSchema])
def list_integrations(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    integrations = db.query(DBIntegration).filter(DBIntegration.tenant_id == current_user.tenant_id).order_by(DBIntegration.created_at.desc()).all()
    return integrations

@router.post("", response_model=IntegrationSchema)
def create_integration(payload: IntegrationCreateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    integration = DBIntegration(
        tenant_id=current_user.tenant_id,
        provider=payload.provider,
        name=payload.name,
        config=payload.config,
        enabled=True
    )
    db.add(integration)
    db.commit()
    db.refresh(integration)
    return integration

@router.delete("/{integration_id}")
def delete_integration(integration_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    integration = db.query(DBIntegration).filter(
        DBIntegration.id == integration_id,
        DBIntegration.tenant_id == current_user.tenant_id
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    db.delete(integration)
    db.commit()
    return {"status": "deleted"}
