import datetime
import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.dashboards.models import DashboardCreateSchema, DashboardSchema, DashboardUpdateSchema
from denoiser.storage.db import Dashboard as DBDashboard
from denoiser.storage.db import get_db

router = APIRouter(prefix="/dashboards", tags=["dashboards"])

@router.get("", response_model=list[DashboardSchema])
def list_dashboards(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    # Only show shared dashboards or user's own dashboards
    dashboards = db.query(DBDashboard).filter(
        DBDashboard.tenant_id == current_user.tenant_id,
        ((DBDashboard.is_shared) | (DBDashboard.user_id == current_user.id))
    ).all()
    return dashboards

@router.get("/{dashboard_id}", response_model=DashboardSchema)
def get_dashboard(dashboard_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    dashboard = db.query(DBDashboard).filter(DBDashboard.id == dashboard_id).first()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    if not dashboard.is_shared and dashboard.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this dashboard")

    return dashboard

@router.post("", response_model=DashboardSchema)
def create_dashboard(payload: DashboardCreateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    # For some reason, pydantic dicts come through directly sometimes, but let's ensure json safety
    db_dash = DBDashboard(
        name=payload.name,
        user_id=current_user.id,
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

    if dashboard.user_id != current_user.id and current_user.role != "ADMIN":
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

    if dashboard.user_id != current_user.id and current_user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Not authorized to delete this dashboard")

    db.delete(dashboard)
    db.commit()
    return {"status": "deleted"}

@router.get("/{dashboard_id}/widgets/{widget_id}/data")
def get_widget_data(dashboard_id: int, widget_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
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

    w_type = widget.get("type")

    if w_type == "metric_card":
        return {"value": random.randint(100, 10000)}

    elif w_type == "time_series":
        # Generate some mock time series data
        now = datetime.datetime.utcnow()
        points = []
        for i in range(24):
            points.append({
                "timestamp": (now - datetime.timedelta(hours=24-i)).isoformat(),
                "value": random.randint(10, 100)
            })
        return {"series": [{"name": widget.get("title", "Metric"), "data": points}]}

    elif w_type == "log_table":
        # Mock recent logs
        logs = []
        for i in range(10):
            logs.append({
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "level": random.choice(["INFO", "ERROR", "WARN"]),
                "service": "api-gateway",
                "message": f"Sample log entry {i}"
            })
        return {"logs": logs}

    return {"error": "Unknown widget type"}
