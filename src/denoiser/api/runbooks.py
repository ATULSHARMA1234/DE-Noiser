from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role

# Runbook execution and alert routing are a paid capability. Gated at the
# router so every route inherits it — including any added later, which is the
# failure mode of per-route gating.
from denoiser.api.entitlements import FEATURE_AUTOMATION, require_feature
from denoiser.api.pagination import ResourceId
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.automation.step_secrets import SECRET_MASK, masked_steps, seal_steps
from denoiser.storage.db import Runbook, RunbookExecution, get_db
from denoiser.utils.time import iso_utc

router = APIRouter(
    prefix="/runbooks",
    tags=["runbooks"],
    dependencies=[Depends(require_feature(FEATURE_AUTOMATION))],
)

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

    model_config = ConfigDict(from_attributes=True)

class RunbookExecutionResponseSchema(BaseModel):
    id: int
    runbook_id: ResourceId
    incident_id: int | None
    status: str
    logs: list[str]
    created_at: str

    model_config = ConfigDict(from_attributes=True)


#: The keys in a runbook step that name somewhere this process will connect to.
_DESTINATION_KEYS = ("url", "slack_webhook_url", "jira_url")


def _public(rb: Runbook) -> dict[str, Any]:
    return {
        "id": rb.id,
        "name": rb.name,
        "trigger_condition": rb.trigger_condition or {},
        "steps": masked_steps(rb.steps),
        "enabled": rb.enabled,
    }


def _reject_disallowed_destinations(steps: list[dict[str, Any]] | None) -> None:
    """Refuse to save a runbook that points at somewhere we must not fetch.

    The authoritative check is at execution time — DNS is mutable, so a name
    that is public now can be private later, and `automation.engine` re-resolves
    on every step. This one exists so the author finds out while they are still
    looking at the form, instead of discovering it in an execution log days
    later when an incident fired.
    """
    from denoiser.integrations.net_guard import DestinationNotAllowed, validate_destination

    for index, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        for key in _DESTINATION_KEYS:
            value = step.get(key)
            # The mask stands for the stored value, which was checked when it
            # was saved and is re-checked at execution.
            if not value or value == SECRET_MASK:
                continue
            try:
                validate_destination(value)
            except DestinationNotAllowed as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Step {index + 1}: {key} is not an allowed destination: {exc}",
                )


@router.get("", response_model=list[RunbookResponseSchema])
def list_runbooks(scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    runbooks = scope.query(Runbook).order_by(Runbook.created_at.desc()).all()
    return [_public(rb) for rb in runbooks]

@router.post("", response_model=RunbookResponseSchema)
def create_runbook(payload: RunbookCreateSchema, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    _reject_disallowed_destinations(payload.steps)
    rb = Runbook(
        name=payload.name,
        trigger_condition=payload.trigger_condition,
        steps=seal_steps(payload.steps),
        enabled=payload.enabled
    )
    scope.add(rb)
    db.commit()
    db.refresh(rb)
    return _public(rb)

class RunbookUpdateSchema(BaseModel):
    name: str | None = None
    trigger_condition: dict[str, Any] | None = None
    steps: list[dict[str, Any]] | None = None
    enabled: bool | None = None


class RunbookRunSchema(BaseModel):
    # Runs against a real incident when given one, so steps that quote incident
    # fields (webhook bodies, Slack messages) carry real content.
    incident_id: int | None = None


@router.put("/{runbook_id}", response_model=RunbookResponseSchema)
def update_runbook(
    runbook_id: ResourceId,
    payload: RunbookUpdateSchema,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    """Update a runbook — used by the UI's enable/disable toggle and editing."""
    rb = scope.get_or_404(Runbook, runbook_id, "Runbook not found")

    changes = payload.model_dump(exclude_unset=True)
    if "steps" in changes:
        _reject_disallowed_destinations(changes["steps"])
        changes["steps"] = seal_steps(changes["steps"], previous=rb.steps)

    for field, value in changes.items():
        setattr(rb, field, value)

    db.commit()
    db.refresh(rb)
    return _public(rb)


@router.post("/{runbook_id}/run", response_model=dict)
def run_runbook_now(
    runbook_id: ResourceId,
    payload: RunbookRunSchema | None = None,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    """Execute a runbook immediately, without waiting for a matching incident.

    A runbook could only ever be fired by the incident trigger, so there was no
    way to try one you had just written — you wrote steps, saved them, and hoped.
    """
    from denoiser.automation.engine import run_runbook
    from denoiser.storage.db import Incident

    rb = scope.get_or_404(Runbook, runbook_id, "Runbook not found")

    incident = None
    if payload and payload.incident_id:
        incident = scope.query(Incident).filter(
            Incident.id == payload.incident_id
        ).first()
        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")
    else:
        # Most recent open incident gives the steps something real to report;
        # with none at all the run still proceeds against an empty context.
        incident = (
            scope.query(Incident)
            .filter(Incident.status == "OPEN")
            .order_by(Incident.created_at.desc())
            .first()
        )

    execution = run_runbook(
        db, rb, incident,
        reason=f"Manual execution by {current_user.email}",
    )
    return {
        "execution_id": execution.id,
        "runbook_id": rb.id,
        "incident_id": execution.incident_id,
        "status": execution.status,
        "logs": execution.logs,
    }


@router.delete("/{runbook_id}")
def delete_runbook(runbook_id: ResourceId, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["ADMIN"]))):
    rb = scope.get_or_404(Runbook, runbook_id, "Runbook not found")
    db.delete(rb)
    db.commit()
    return {"status": "deleted"}

@router.get("/executions", response_model=list[dict])
def list_executions(db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    executions = (
        db.query(RunbookExecution)
        .select_from(RunbookExecution)
        .join(Runbook, Runbook.id == RunbookExecution.runbook_id)
        .filter(scope.predicate(Runbook))
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
            "created_at": iso_utc(ex.created_at)
        } for ex in executions
    ]
