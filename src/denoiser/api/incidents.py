"""Incident CRUD and drill-down."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from denoiser.api.abac import require_abac
from denoiser.api.auth import require_role
from denoiser.api.pagination import ResourceId
from denoiser.api.schemas import ResolveRequest
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.storage.db import Incident, User, get_db

router = APIRouter(tags=["incidents"])

# ─── INCIDENTS — CRUD + drill-down ───────────────────────────────────────────

@router.get("/incidents")
def get_incidents(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    scope: TenantScope = Depends(tenant_scope),
):
    incidents = (
        scope.query(Incident)
        .order_by(Incident.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [_incident_to_dict(inc) for inc in incidents]


@router.get("/incidents/{incident_id}")
def get_incident_detail(incident_id: ResourceId, db: Session = Depends(get_db), current_user: User = Depends(require_abac("read", "incident")), scope: TenantScope = Depends(tenant_scope)):
    inc = scope.query(Incident).filter(Incident.id == incident_id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _incident_to_dict(inc)


@router.put("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: ResourceId, body: ResolveRequest, db: Session = Depends(get_db), current_user: User = Depends(require_abac("write", "incident")), scope: TenantScope = Depends(tenant_scope)):
    inc = scope.query(Incident).filter(Incident.id == incident_id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    inc.status = "RESOLVED" if body.resolved else "OPEN"
    if body.resolved:
        from denoiser.utils.time import utcnow
        inc.resolved_at = utcnow()
    else:
        inc.resolved_at = None
    db.commit()
    return _incident_to_dict(inc)


@router.delete("/incidents/{incident_id}")
def delete_incident(incident_id: ResourceId, db: Session = Depends(get_db), current_user: User = Depends(require_abac("delete", "incident")), scope: TenantScope = Depends(tenant_scope)):
    inc = scope.query(Incident).filter(Incident.id == incident_id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    db.delete(inc)
    db.commit()
    return {"status": "deleted", "id": incident_id}


def _incident_to_dict(inc: Incident) -> dict:
    return {
        "id": inc.id,
        "status": inc.status,
        "title": inc.title,
        "domain": inc.domain,
        "impact_score": inc.impact_score,
        "created_at": inc.created_at.isoformat() if inc.created_at else None,
        "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
        "summary": inc.summary,
        "remediation_hints": inc.remediation_hints,
        "run_id": inc.run_id if hasattr(inc, "run_id") else None,
        "source": inc.source if hasattr(inc, "source") else None,
        "total_logs": inc.total_logs if hasattr(inc, "total_logs") else None,
        "cluster_count": inc.cluster_count if hasattr(inc, "cluster_count") else None,
    }


