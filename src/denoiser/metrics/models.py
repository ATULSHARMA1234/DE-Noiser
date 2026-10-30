from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MetricRuleCreateSchema(BaseModel):
    name: str
    query: str
    aggregation: str = "count"
    window_seconds: int = 60

class MetricRuleSchema(BaseModel):
    id: int
    tenant_id: int | None = None
    name: str
    query: str
    aggregation: str
    window_seconds: int
    enabled: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ExtractedMetricSchema(BaseModel):
    id: int
    rule_id: int
    timestamp: datetime
    value: float

    model_config = ConfigDict(from_attributes=True)
