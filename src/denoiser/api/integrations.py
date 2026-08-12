from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.api.pagination import ResourceId
from denoiser.api.scope import TenantScope, tenant_scope
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
def list_integrations(db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    integrations = scope.query(DBIntegration).order_by(DBIntegration.created_at.desc()).all()
    # Expire so the masking below is never flushed back to the database.
    for integration in integrations:
        db.expunge(integration)
    return [_redact(i) for i in integrations]

@router.post("", response_model=IntegrationSchema)
def create_integration(payload: IntegrationCreateSchema, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["ADMIN"]))):
    integration = DBIntegration(
        provider=payload.provider,
        name=payload.name,
        config=payload.config,
        enabled=True
    )
    scope.add(integration)
    db.commit()
    db.refresh(integration)
    db.expunge(integration)
    return _redact(integration)

@router.put("/{integration_id}", response_model=IntegrationSchema)
def update_integration(
    integration_id: ResourceId,
    payload: IntegrationUpdateSchema,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Update a connected integration (the UI's "Configure" action).

    Without this, a connected integration could only be deleted and recreated —
    which is why the Configure button had nothing to call.
    """
    integration = db.query(DBIntegration).filter(
        DBIntegration.id == integration_id,
        scope.predicate(DBIntegration),
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

def _provider_for(integration: DBIntegration):
    """Build the client for a stored integration row.

    GitHubIntegration was fully implemented and never instantiated anywhere —
    the marketplace stored a row and nothing ever used it. This is the join
    between the two.
    """
    provider = (integration.provider or "").lower()
    config = integration.config or {}

    if provider == "github":
        from denoiser.integrations.github import GitHubIntegration

        return GitHubIntegration(
            api_token=config.get("api_key") or config.get("token") or "",
            repo=config.get("repo"),
        )
    return None


@router.post("/{integration_id}/test", response_model=dict[str, Any])
def test_integration(
    integration_id: ResourceId,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    """Check that a stored integration's credentials actually work.

    Connecting an integration only wrote a row; whether the token was valid was
    discovered later, when an alert silently failed to deliver.
    """
    integration = db.query(DBIntegration).filter(
        DBIntegration.id == integration_id,
        scope.predicate(DBIntegration),
    ).first()
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    client = _provider_for(integration)
    if client is None:
        return {
            "status": "unsupported",
            "detail": f"No connectivity check implemented for '{integration.provider}'",
        }

    try:
        metadata = client.sync_metadata()
    except Exception as e:
        return {"status": "failed", "detail": str(e)}

    return {
        "status": "ok",
        "provider": client.get_provider_name(),
        "detail": f"Reached {metadata.get('repo')} (default branch {metadata.get('default_branch')})",
    }


@router.post("/{integration_id}/sync", response_model=dict[str, Any])
def sync_integration(
    integration_id: ResourceId,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    """Pull deployment metadata from the provider into the deployment markers.

    This is what makes "correlate deployments with anomalies" real: GitHub
    deployments become DeploymentMarker rows the Metrics page already renders.
    Existing markers are matched on (service, version, environment) so a repeat
    sync updates rather than duplicates.
    """
    from denoiser.storage.db import DeploymentMarker

    integration = db.query(DBIntegration).filter(
        DBIntegration.id == integration_id,
        scope.predicate(DBIntegration),
    ).first()
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    client = _provider_for(integration)
    if client is None:
        raise HTTPException(
            status_code=400,
            detail=f"Metadata sync is not implemented for '{integration.provider}'",
        )

    try:
        metadata = client.sync_metadata()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Sync failed: {e}")

    repo = metadata.get("repo") or integration.name
    config = integration.config or {}

    # Which service a deployment belongs to. Deriving it from the repo name is a
    # fine default and wrong for a monorepo, where every service would collapse
    # into one marker series — so it is overridable, and a per-environment map
    # covers repos that deploy different services to different environments.
    default_service = config.get("service") or (repo.split("/")[-1] if repo else "unknown")
    service_by_environment = config.get("service_by_environment") or {}
    imported = 0

    for deployment in metadata.get("deployments", []):
        version = (deployment.get("sha") or "")[:12] or deployment.get("ref") or "unknown"
        environment = deployment.get("environment") or "production"
        service = service_by_environment.get(environment, default_service)
        existing = db.query(DeploymentMarker).filter(
            scope.predicate(DeploymentMarker),
            DeploymentMarker.service == service,
            DeploymentMarker.version == version,
            DeploymentMarker.environment == environment,
        ).first()
        if existing:
            continue

        db.add(DeploymentMarker(
            tenant_id=scope.tenant_id,
            service=service,
            version=version,
            environment=environment,
            description=deployment.get("description") or f"{repo} @ {deployment.get('ref')}",
            timestamp=_parse_github_time(deployment.get("created_at")),
        ))
        imported += 1

    integration.config = {
        **(integration.config or {}),
        "last_synced_at": metadata.get("synced_at"),
        "default_branch": metadata.get("default_branch"),
        "latest_release": (metadata.get("latest_release") or {}).get("tag"),
    }
    db.commit()

    return {
        "status": "synced",
        "repo": repo,
        "service": default_service,
        "deployments_imported": imported,
        "deployments_seen": len(metadata.get("deployments", [])),
        "latest_release": (metadata.get("latest_release") or {}).get("tag"),
        "synced_at": metadata.get("synced_at"),
    }


def _parse_github_time(value: str | None):
    """GitHub timestamps are ISO-8601 with a Z; markers store naive UTC."""
    from datetime import datetime

    from denoiser.utils.time import utcnow

    if not value:
        return utcnow()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return utcnow()
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


@router.delete("/{integration_id}")
def delete_integration(integration_id: ResourceId, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["ADMIN"]))):
    integration = db.query(DBIntegration).filter(
        DBIntegration.id == integration_id,
        scope.predicate(DBIntegration)
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    db.delete(integration)
    db.commit()
    return {"status": "deleted"}
