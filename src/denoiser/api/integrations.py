from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.storage.db import Integration as DBIntegration
from denoiser.storage.db import get_db

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Config keys whose values are credentials. They are stored so the integration
# can actually authenticate, but never echoed back — a GET that returns the
# token it was given turns any read-access account into a credential exfiltration
# path, and the UI only ever needs to know whether one is set.
SECRET_CONFIG_KEYS = ("token", "key", "secret", "password", "credential")
MASK = "••••••••"


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_CONFIG_KEYS)


def _redact(integration: DBIntegration) -> DBIntegration:
    """Mask credential values in-place on a detached read copy."""
    config = dict(integration.config or {})
    for key, value in config.items():
        if _is_secret(key) and value:
            config[key] = MASK
    integration.config = config
    return integration


def _merge_config(existing: dict | None, incoming: dict | None) -> dict:
    """Merge an update, treating a masked secret as "leave it alone".

    The UI renders the masked value it was given; sending it back must not
    overwrite the real credential with a row of dots.
    """
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if _is_secret(key) and value == MASK:
            continue
        merged[key] = value
    return merged


class IntegrationCreateSchema(BaseModel):
    provider: str
    name: str
    config: dict[str, Any]


class IntegrationUpdateSchema(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None

class IntegrationSchema(BaseModel):
    id: int
    provider: str
    name: str
    config: dict[str, Any]
    enabled: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

@router.get("", response_model=list[IntegrationSchema])
def list_integrations(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    integrations = db.query(DBIntegration).filter(DBIntegration.tenant_id == current_user.tenant_id).order_by(DBIntegration.created_at.desc()).all()
    # Expire so the masking below is never flushed back to the database.
    for integration in integrations:
        db.expunge(integration)
    return [_redact(i) for i in integrations]

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
    db.expunge(integration)
    return _redact(integration)

@router.put("/{integration_id}", response_model=IntegrationSchema)
def update_integration(
    integration_id: int,
    payload: IntegrationUpdateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Update a connected integration (the UI's "Configure" action).

    Without this, a connected integration could only be deleted and recreated —
    which is why the Configure button had nothing to call.
    """
    integration = db.query(DBIntegration).filter(
        DBIntegration.id == integration_id,
        DBIntegration.tenant_id == current_user.tenant_id,
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    if payload.name is not None:
        integration.name = payload.name
    if payload.config is not None:
        integration.config = _merge_config(integration.config, payload.config)
    if payload.enabled is not None:
        integration.enabled = payload.enabled

    db.commit()
    db.refresh(integration)
    db.expunge(integration)
    return _redact(integration)

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
