import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.dashboards.models import DashboardCreateSchema, DashboardSchema, DashboardUpdateSchema
from denoiser.storage.db import Dashboard as DBDashboard
from denoiser.storage.db import AnalysisRun, Incident
from denoiser.storage.db import ServiceLevelObjective as SLO
from denoiser.storage.db import get_db

router = APIRouter(prefix="/dashboards", tags=["dashboards"])

@router.get("", response_model=list[DashboardSchema])
def list_dashboards(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    # Dashboards are scoped per tenant (the model has no per-user owner column).
    dashboards = db.query(DBDashboard).filter(
        DBDashboard.tenant_id == current_user.tenant_id
    ).all()
    return dashboards

@router.get("/{dashboard_id}", response_model=DashboardSchema)
def get_dashboard(dashboard_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    dashboard = db.query(DBDashboard).filter(DBDashboard.id == dashboard_id).first()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    if dashboard.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this dashboard")

    return dashboard

@router.post("", response_model=DashboardSchema)
def create_dashboard(payload: DashboardCreateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    # For some reason, pydantic dicts come through directly sometimes, but let's ensure json safety
    db_dash = DBDashboard(
        name=payload.name,
        tenant_id=current_user.tenant_id,
        layout=[layout_item for layout_item in payload.layout],
        widgets=[w.dict() for w in payload.widgets],
        is_shared=payload.is_shared
    )
    db.add(db_dash)
    db.commit()
    db.refresh(db_dash)
    return db_dash

@router.put("/{dashboard_id}", response_model=DashboardSchema)
def update_dashboard(dashboard_id: int, payload: DashboardUpdateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    dashboard = db.query(DBDashboard).filter(DBDashboard.id == dashboard_id).first()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    if dashboard.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this dashboard")

    if payload.name is not None:
        dashboard.name = payload.name
    if payload.layout is not None:
        dashboard.layout = payload.layout
    if payload.widgets is not None:
        dashboard.widgets = [w.dict() for w in payload.widgets]
    if payload.is_shared is not None:
        dashboard.is_shared = payload.is_shared

    db.commit()
    db.refresh(dashboard)
    return dashboard

@router.delete("/{dashboard_id}")
def delete_dashboard(dashboard_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    dashboard = db.query(DBDashboard).filter(DBDashboard.id == dashboard_id).first()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    if dashboard.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this dashboard")

    db.delete(dashboard)
    db.commit()
    return {"status": "deleted"}

@router.get("/{dashboard_id}/widgets/{widget_id}/data")
def get_widget_data(
    dashboard_id: int,
    widget_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
    variables: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """
    Fetch the underlying data for a specific widget.
    In a real system, this would execute the widget's config (e.g. log query, metric query).
    For this demo, we mock data based on widget type.
    """
    dashboard = db.query(DBDashboard).filter(DBDashboard.id == dashboard_id).first()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    widget = next((w for w in dashboard.widgets if w.get("id") == widget_id), None)
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")

    # Normalize legacy widget-type aliases to the canonical names.
    _ALIAS = {"stat": "metric_card", "timeseries": "time_series", "logs": "incident_feed"}
    w_type = _ALIAS.get(widget.get("type"), widget.get("type"))
    config = widget.get("config") or {}
    metric = config.get("metric")
    tenant = current_user.tenant_id

    def _incident_q():
        return db.query(Incident).filter(Incident.tenant_id == tenant)

    if w_type == "metric_card":
        # Each metric is computed live from real tenant data.
        if metric == "total_incidents":
            return {"value": _incident_q().count(), "label": "Total Incidents"}
        if metric == "resolved_incidents":
            return {"value": _incident_q().filter(Incident.status == "RESOLVED").count(), "label": "Resolved", "tone": "ok"}
        if metric == "avg_impact":
            avg = db.query(func.avg(Incident.impact_score)).filter(Incident.tenant_id == tenant).scalar()
            return {"value": round(float(avg or 0), 2), "label": "Avg Impact", "tone": "warn"}
        if metric == "slos_tracked":
            return {"value": db.query(SLO).filter(SLO.tenant_id == tenant).count(), "label": "SLOs Tracked"}
        if metric == "clusters_last_run":
            run = db.query(AnalysisRun).filter(AnalysisRun.tenant_id == tenant).order_by(AnalysisRun.created_at.desc()).first()
            return {"value": (run.cluster_count if run else 0), "label": "Clusters (last run)"}
        if metric == "runs_total":
            return {"value": db.query(AnalysisRun).filter(AnalysisRun.tenant_id == tenant).count(), "label": "Analysis Runs"}
        # default: open incidents
        return {"value": _incident_q().filter(Incident.status == "OPEN").count(), "label": "Open Incidents", "tone": "crit"}

    elif w_type == "time_series":
        # Incidents opened per day over the last 14 days (real).
        now = datetime.datetime.utcnow()
        days = 14
        buckets = {(now.date() - datetime.timedelta(days=days - 1 - i)).isoformat(): 0 for i in range(days)}
        since = now - datetime.timedelta(days=days)
        for (created,) in db.query(Incident.created_at).filter(Incident.tenant_id == tenant, Incident.created_at >= since):
            if created:
                key = created.date().isoformat()
                if key in buckets:
                    buckets[key] += 1
        points = [{"timestamp": k, "value": v} for k, v in buckets.items()]
        return {"series": [{"name": widget.get("title", "Incidents / day"), "data": points}]}

    elif w_type == "bar":
        # Top domains by incident count (real).
        rows = (
            db.query(Incident.domain, func.count(Incident.id))
            .filter(Incident.tenant_id == tenant)
            .group_by(Incident.domain)
            .order_by(func.count(Incident.id).desc())
            .limit(8)
            .all()
        )
        return {"bars": [{"label": (d or "unknown"), "value": c} for d, c in rows]}

    elif w_type == "gauge":
        # Resolved ratio: share of incidents that are resolved (0..1) vs a target.
        total = _incident_q().count()
        resolved = _incident_q().filter(Incident.status == "RESOLVED").count()
        ratio = (resolved / total) if total else 0.0
        return {"value": round(ratio, 4), "label": "Resolved Ratio", "target": 0.8, "resolved": resolved, "total": total}

    elif w_type == "heatmap":
        # Incidents by weekday (0=Mon) x hour (0..23) — reveals when things break.
        cells = [[0] * 24 for _ in range(7)]
        mx = 0
        for (created,) in db.query(Incident.created_at).filter(Incident.tenant_id == tenant):
            if created:
                d, h = created.weekday(), created.hour
                cells[d][h] += 1
                mx = max(mx, cells[d][h])
        flat = [{"day": d, "hour": h, "value": cells[d][h]} for d in range(7) for h in range(24) if cells[d][h]]
        return {"cells": flat, "max": mx}

    elif w_type in ("incident_feed", "log_table"):
        # Most recent incidents (real). log_table kept for back-compat.
        rows = _incident_q().order_by(Incident.created_at.desc()).limit(12).all()
        return {"incidents": [
            {
                "id": r.id,
                "title": r.title,
                "status": r.status,
                "domain": r.domain,
                "impact_score": r.impact_score,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]}

    return {"error": "Unknown widget type"}
