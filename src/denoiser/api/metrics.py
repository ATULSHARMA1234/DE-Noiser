import datetime
import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.metrics.models import MetricRuleCreateSchema, MetricRuleSchema
from denoiser.storage.db import ExtractedMetric as DBExtractedMetric
from denoiser.storage.db import MetricRule as DBMetricRule
from denoiser.storage.db import get_db

router = APIRouter(prefix="/metrics", tags=["metrics"])

@router.get("/rules", response_model=list[MetricRuleSchema])
def list_metric_rules(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    rules = db.query(DBMetricRule).order_by(DBMetricRule.created_at.desc()).all()
    return rules

@router.post("/rules", response_model=MetricRuleSchema)
def create_metric_rule(payload: MetricRuleCreateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    rule = DBMetricRule(
        name=payload.name,
        query=payload.query,
        aggregation=payload.aggregation,
        window_seconds=payload.window_seconds
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule

@router.delete("/rules/{rule_id}")
def delete_metric_rule(rule_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    rule = db.query(DBMetricRule).filter(DBMetricRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    db.delete(rule)
    db.commit()
    return {"status": "deleted"}

@router.get("/rules/{rule_id}/data")
def get_metric_data(rule_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    rule = db.query(DBMetricRule).filter(DBMetricRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    metrics = db.query(DBExtractedMetric).filter(DBExtractedMetric.rule_id == rule_id).order_by(DBExtractedMetric.timestamp.asc()).all()

    # If no data exists yet, mock some data for the sandbox
    if not metrics:
        now = datetime.datetime.utcnow()
        points = []
        base_val = random.randint(10, 50)
        for i in range(24):
            val = max(0, base_val + random.randint(-5, 5))
            points.append({
                "timestamp": (now - datetime.timedelta(hours=24-i)).isoformat(),
                "value": val
            })
        return {"rule_name": rule.name, "data": points}

    return {
        "rule_name": rule.name,
        "data": [{"timestamp": m.timestamp.isoformat(), "value": m.value} for m in metrics]
    }
