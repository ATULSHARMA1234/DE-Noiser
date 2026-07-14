import contextlib
import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.dashboards.models import DashboardCreateSchema, DashboardSchema, DashboardUpdateSchema
from denoiser.storage.db import AnalysisRun, Incident, ServiceLevelObjective, get_db
from denoiser.storage.db import Dashboard as DBDashboard

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
        is_shared=payload.is_shared,
        default_time_range=payload.default_time_range,
        template_variables=payload.template_variables
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
    if payload.default_time_range is not None:
        dashboard.default_time_range = payload.default_time_range
    if payload.template_variables is not None:
        dashboard.template_variables = payload.template_variables

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
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))
):
    """
    Fetch the underlying data for a specific widget, computed from the tenant's
    own rows. Every branch is scoped to current_user.tenant_id.
    """
    dashboard = db.query(DBDashboard).filter(DBDashboard.id == dashboard_id).first()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    if dashboard.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this dashboard")

    widget = next((w for w in dashboard.widgets if w.get("id") == widget_id), None)
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")

    # Normalize widget-type aliases. The UI's widget picker emits bar_chart /
    # pie_chart / markdown, so those must resolve here -- an unknown type 400s,
    # which would leave those widgets stuck on their loading state forever.
    _ALIAS = {
        "stat": "metric_card",
        "timeseries": "time_series",
        "logs": "incident_feed",
        "bar_chart": "bar",
        "log_table": "incident_feed",
    }
    w_type = _ALIAS.get(widget.get("type"), widget.get("type"))
    config = widget.get("config") or {}
    metric = config.get("metric")
    tenant = current_user.tenant_id

    # The dashboard's time picker sends start_time as a relative window (15m, 1h,
    # 4h, 1d, 7d). It was accepted and then ignored, so changing the range moved
    # the control but never the numbers. Resolve it to a cutoff and apply it.
    _UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    since = None
    if start_time:
        raw = start_time.strip().lstrip("-")
        if raw and raw[-1].lower() in _UNITS and raw[:-1].isdigit():
            seconds = int(raw[:-1]) * _UNITS[raw[-1].lower()]
            since = datetime.datetime.utcnow() - datetime.timedelta(seconds=seconds)
        else:
            with contextlib.suppress(ValueError):
                since = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)

    def _incident_q():
        q = db.query(Incident).filter(Incident.tenant_id == tenant)
        if since is not None:
            q = q.filter(Incident.created_at >= since)
        return q

    if w_type == "metric_card":
        metrics = {
            "open_incidents": lambda: {
                "value": _incident_q().filter(Incident.status == "OPEN").count(),
                "label": "Open Incidents", "tone": "crit",
            },
            "total_incidents": lambda: {"value": _incident_q().count(), "label": "Total Incidents"},
            "resolved_incidents": lambda: {
                "value": _incident_q().filter(Incident.status == "RESOLVED").count(),
                "label": "Resolved", "tone": "ok",
            },
            "avg_impact": lambda: {
                "value": round(float(
                    _incident_q().with_entities(func.avg(Incident.impact_score)).scalar() or 0
                ), 2),
                "label": "Avg Impact", "tone": "warn",
            },
            "slos_tracked": lambda: {
                "value": db.query(ServiceLevelObjective).filter(ServiceLevelObjective.tenant_id == tenant).count(),
                "label": "SLOs Tracked",
            },
            "clusters_last_run": lambda: {
                "value": (lambda r: r.cluster_count if r else 0)(
                    db.query(AnalysisRun).filter(AnalysisRun.tenant_id == tenant)
                    .order_by(AnalysisRun.created_at.desc()).first()
                ),
                "label": "Clusters (last run)",
            },
            "runs_total": lambda: {
                "value": db.query(AnalysisRun).filter(AnalysisRun.tenant_id == tenant).count(),
                "label": "Analysis Runs",
            },
        }
        if metric and metric not in metrics:
            raise HTTPException(status_code=400, detail=f"Unknown metric '{metric}'")
        return metrics[metric or "open_incidents"]()

    elif w_type == "time_series":
        # Incidents opened per day over the last 14 days.
        now = datetime.datetime.utcnow()
        days = 14
        since = now - datetime.timedelta(days=days)
        buckets = {(now.date() - datetime.timedelta(days=days - 1 - i)).isoformat(): 0 for i in range(days)}
        rows = (
            db.query(func.date(Incident.created_at), func.count(Incident.id))
            .filter(Incident.tenant_id == tenant, Incident.created_at >= since)
            .group_by(func.date(Incident.created_at))
            .all()
        )
        for day, count in rows:
            key = day.isoformat() if hasattr(day, "isoformat") else str(day)
            if key in buckets:
                buckets[key] = count
        points = [{"timestamp": k, "value": v} for k, v in buckets.items()]
        return {"series": [{"name": widget.get("title", "Incidents / day"), "data": points}]}

    elif w_type == "bar":
        rows = (
            _incident_q()
            .with_entities(Incident.domain, func.count(Incident.id))
            .group_by(Incident.domain)
            .order_by(func.count(Incident.id).desc())
            .limit(8)
            .all()
        )
        return {"bars": [{"label": (d or "unknown"), "value": c} for d, c in rows]}

    elif w_type == "gauge":
        total = _incident_q().count()
        resolved = _incident_q().filter(Incident.status == "RESOLVED").count()
        ratio = (resolved / total) if total else 0.0
        return {"value": round(ratio, 4), "label": "Resolved Ratio", "target": 0.8,
                "resolved": resolved, "total": total}

    elif w_type == "heatmap":
        # Incidents by weekday x hour, aggregated in SQL and bounded to a window
        # so this does not full-scan every incident the tenant has ever had.
        try:
            days = int(config.get("days") or 30)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="heatmap 'days' must be an integer")
        # Keep the window genuinely bounded -- an arbitrarily large value would
        # turn this back into a full scan of every incident the tenant ever had.
        days = max(1, min(days, 90))
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        rows = (
            db.query(
                func.extract("dow", Incident.created_at).label("dow"),
                func.extract("hour", Incident.created_at).label("hour"),
                func.count(Incident.id),
            )
            .filter(Incident.tenant_id == tenant, Incident.created_at >= since)
            .group_by("dow", "hour")
            .all()
        )
        cells, mx = [], 0
        for dow, hour, count in rows:
            # Postgres dow: 0=Sunday..6=Saturday -> shift to 0=Monday..6=Sunday
            day = (int(dow) - 1) % 7
            cells.append({"day": day, "hour": int(hour), "value": count})
            mx = max(mx, count)
        return {"cells": cells, "max": mx, "days": days}

    elif w_type == "pie_chart":
        # Distribution of incidents across a dimension (default: severity band).
        by = config.get("by") or "severity"
        if by == "status":
            rows = (
                _incident_q()
                .with_entities(Incident.status, func.count(Incident.id))
                .group_by(Incident.status)
                .all()
            )
            slices = [{"name": (s or "UNKNOWN").title(), "value": c} for s, c in rows]
        elif by == "domain":
            rows = (
                _incident_q()
                .with_entities(Incident.domain, func.count(Incident.id))
                .group_by(Incident.domain)
                .order_by(func.count(Incident.id).desc())
                .limit(6)
                .all()
            )
            slices = [{"name": (d or "unknown"), "value": c} for d, c in rows]
        else:
            # Severity bands off the impact score.
            bands = [
                ("Critical", 0.8, 1.01),
                ("High", 0.6, 0.8),
                ("Medium", 0.3, 0.6),
                ("Low", 0.0, 0.3),
            ]
            slices = []
            for name, lo, hi in bands:
                c = (
                    _incident_q()
                    .filter(Incident.impact_score >= lo, Incident.impact_score < hi)
                    .count()
                )
                if c:
                    slices.append({"name": name, "value": c})
        return {"slices": slices, "by": by}

    elif w_type == "markdown":
        # Content lives in the widget config; there is nothing to query. Return a
        # payload anyway so the client's "data loaded" check passes.
        return {"content": config.get("content", "")}

    elif w_type in ("incident_feed", "log_table"):
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

    raise HTTPException(status_code=400, detail=f"Unknown widget type '{widget.get('type')}'")
