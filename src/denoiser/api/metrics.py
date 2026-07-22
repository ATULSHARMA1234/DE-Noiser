from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.metrics.models import MetricRuleCreateSchema, MetricRuleSchema
from denoiser.storage.db import ExtractedMetric as DBExtractedMetric
from denoiser.storage.db import MetricRule as DBMetricRule
from denoiser.storage.db import get_db

router = APIRouter(prefix="/metrics", tags=["metrics"])

@router.get("/rules", response_model=list[MetricRuleSchema])
def list_metric_rules(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))
):
    query = db.query(DBMetricRule)
    if current_user.tenant_id:
        query = query.filter(
            (DBMetricRule.tenant_id == current_user.tenant_id) |
            (DBMetricRule.tenant_id.is_(None))  # include legacy un-scoped rules
        )
    return query.order_by(DBMetricRule.created_at.desc()).all()

@router.post("/rules", response_model=MetricRuleSchema)
def create_metric_rule(
    payload: MetricRuleCreateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))
):
    rule = DBMetricRule(
        tenant_id=current_user.tenant_id,
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
def delete_metric_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"]))
):
    rule = db.query(DBMetricRule).filter(DBMetricRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if current_user.tenant_id and rule.tenant_id and rule.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    db.delete(rule)
    db.commit()
    return {"status": "deleted"}


@router.get("/rules/{rule_id}/data")
def get_metric_data(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))
):
    rule = db.query(DBMetricRule).filter(DBMetricRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    # Tenant isolation: only allow access to own tenant's rules
    if current_user.tenant_id and rule.tenant_id and rule.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # The rule is tenant-checked above, but the samples carry their own
    # tenant_id: a rule that predates tenant scoping (tenant_id NULL) is
    # readable by everyone, so filtering the rule alone would still hand back
    # another tenant's datapoints.
    metrics_query = db.query(DBExtractedMetric).filter(DBExtractedMetric.rule_id == rule_id)
    if current_user.tenant_id:
        metrics_query = metrics_query.filter(
            (DBExtractedMetric.tenant_id == current_user.tenant_id) |
            (DBExtractedMetric.tenant_id.is_(None))
        )
    metrics = metrics_query.order_by(DBExtractedMetric.timestamp.asc()).all()

    return {
        "rule_name": rule.name,
        "data": [
            {"timestamp": m.timestamp.isoformat(), "value": m.value}
            for m in metrics
        ]
    }
