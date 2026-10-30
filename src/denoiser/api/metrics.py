from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.api.pagination import ResourceId
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.metrics.models import MetricRuleCreateSchema, MetricRuleSchema
from denoiser.storage.db import ExtractedMetric as DBExtractedMetric
from denoiser.storage.db import MetricRule as DBMetricRule
from denoiser.storage.db import get_db

router = APIRouter(prefix="/metrics", tags=["metrics"])

@router.get("/rules", response_model=list[MetricRuleSchema])
def list_metric_rules(
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))
):
    return scope.query(DBMetricRule).order_by(DBMetricRule.created_at.desc()).all()

@router.post("/rules", response_model=MetricRuleSchema)
def create_metric_rule(
    payload: MetricRuleCreateSchema,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))
):
    rule = DBMetricRule(
        name=payload.name,
        query=payload.query,
        aggregation=payload.aggregation,
        window_seconds=payload.window_seconds
    )
    scope.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}")
def delete_metric_rule(
    rule_id: ResourceId,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ADMIN"]))
):
    rule = scope.get_or_404(DBMetricRule, rule_id, "Rule not found")
    db.delete(rule)
    db.commit()
    return {"status": "deleted"}


@router.get("/rules/{rule_id}/data")
def get_metric_data(
    rule_id: ResourceId,
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))
):
    rule = scope.get_or_404(DBMetricRule, rule_id, "Rule not found")

    # Scoped again in its own right, not merely by association: the samples
    # carry their own tenant_id, and trusting the rule's would hand back
    # another organisation's datapoints if the two ever disagreed.
    metrics = (
        scope.query(DBExtractedMetric)
        .filter(DBExtractedMetric.rule_id == rule.id)
        .order_by(DBExtractedMetric.timestamp.asc())
        .all()
    )

    return {
        "rule_name": rule.name,
        "data": [
            {"timestamp": m.timestamp.isoformat(), "value": m.value}
            for m in metrics
        ]
    }
