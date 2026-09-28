import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.api.pagination import ResourceId
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.storage.db import Monitor, get_db
from denoiser.utils.time import iso_utc, utcnow

router = APIRouter(prefix="/monitors", tags=["monitors"])

class MonitorCreateSchema(BaseModel):
    name: str
    type: str = "log alert"
    query: str
    message: str | None = None
    severity: str = "warning"
    threshold_critical: float | None = None
    threshold_warning: float | None = None
    window_seconds: int = 300
    enabled: bool = True

class MonitorUpdateSchema(BaseModel):
    name: str | None = None
    type: str | None = None
    query: str | None = None
    message: str | None = None
    severity: str | None = None
    threshold_critical: float | None = None
    threshold_warning: float | None = None
    window_seconds: int | None = None
    enabled: bool | None = None


def _monitor_to_dict(m: Monitor, now: datetime.datetime) -> dict[str, Any]:
    """Serialize a monitor, including the evaluator's state."""
    return {
        "id": m.id,
        "name": m.name,
        "type": m.type,
        "query": m.query,
        "message": m.message,
        "severity": m.severity,
        "threshold_critical": m.threshold_critical,
        "threshold_warning": m.threshold_warning,
        "window_seconds": m.window_seconds or 300,
        "enabled": m.enabled,
        "muted_until": iso_utc(m.muted_until) if m.muted_until and m.muted_until > now else None,
        "status": m.status or "PENDING",
        "last_value": m.last_value,
        "last_evaluated_at": iso_utc(m.last_evaluated_at),
        "last_triggered_at": iso_utc(m.last_triggered_at),
        "last_error": m.last_error,
        "created_at": iso_utc(m.created_at),
    }

@router.get("", response_model=list[dict[str, Any]])
def list_monitors(scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    monitors = scope.query(Monitor).all()
    now = utcnow()
    return [_monitor_to_dict(m, now) for m in monitors]

@router.post("", response_model=dict[str, Any])
def create_monitor(payload: MonitorCreateSchema, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    m = Monitor(
        name=payload.name,
        type=payload.type,
        query=payload.query,
        message=payload.message,
        severity=payload.severity,
        threshold_critical=payload.threshold_critical,
        threshold_warning=payload.threshold_warning,
        window_seconds=payload.window_seconds,
        enabled=payload.enabled,
        status="PENDING",
    )
    scope.add(m)
    db.commit()
    db.refresh(m)
    return {"status": "created", "id": m.id}

@router.get("/{monitor_id}", response_model=dict[str, Any])
def get_monitor(monitor_id: ResourceId, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    m = scope.get_or_404(Monitor, monitor_id, "Monitor not found")
    return _monitor_to_dict(m, utcnow())

@router.put("/{monitor_id}", response_model=dict[str, Any])
def update_monitor(monitor_id: ResourceId, payload: MonitorUpdateSchema, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    m = scope.get_or_404(Monitor, monitor_id, "Monitor not found")
    
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(m, key, value)
        
    db.commit()
    return {"status": "updated", "id": m.id}

@router.delete("/{monitor_id}", response_model=dict[str, Any])
def delete_monitor(monitor_id: ResourceId, db: Session = Depends(get_db), scope: TenantScope = Depends(tenant_scope), current_user: User = Depends(require_role(["ADMIN"]))):
    m = scope.get_or_404(Monitor, monitor_id, "Monitor not found")
    
    db.delete(m)
    db.commit()
    return {"status": "deleted", "id": m.id}


@router.post("/{monitor_id}/evaluate", response_model=dict[str, Any])
def evaluate_monitor_now(
    monitor_id: ResourceId,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    """Run a monitor's query immediately and return what it found.

    Lets an operator confirm a monitor's query does what they meant before
    waiting for the next scheduled evaluation. The result is persisted like any
    other evaluation, so the status shown in the UI stays consistent.
    """
    from denoiser.monitors.evaluator import apply_result, evaluate_monitor

    m = scope.get_or_404(Monitor, monitor_id, "Monitor not found")

    result = evaluate_monitor(m)
    alerted = apply_result(db, m, result)
    db.commit()
    db.refresh(m)

    return {
        "id": m.id,
        "status": result.status,
        "value": result.value,
        "window_seconds": result.window_seconds,
        "message": result.message,
        "alert_raised": alerted,
        "error": result.error,
        "evaluated_at": iso_utc(m.last_evaluated_at),
    }


class MuteRequest(BaseModel):
    duration_minutes: int  # 0 = unmute

@router.put("/{monitor_id}/mute", response_model=dict[str, Any])
def mute_monitor(
    monitor_id: ResourceId,
    payload: MuteRequest,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))
):
    """Mute (snooze) a monitor for N minutes. Pass 0 to unmute."""
    m = scope.get_or_404(Monitor, monitor_id, "Monitor not found")

    if payload.duration_minutes <= 0:
        m.muted_until = None
    else:
        m.muted_until = utcnow() + datetime.timedelta(minutes=payload.duration_minutes)

    db.commit()
    return {
        "status": "muted" if m.muted_until else "unmuted",
        "id": m.id,
        "muted_until": iso_utc(m.muted_until)
    }
