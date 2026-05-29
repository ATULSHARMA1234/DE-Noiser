from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class MetricRuleCreateSchema(BaseModel):
    name: str
    query: str
    aggregation: str = "count"
    window_seconds: int = 60

class MetricRuleSchema(BaseModel):
    id: int
    name: str
    query: str
    aggregation: str
    window_seconds: int
    enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True

class ExtractedMetricSchema(BaseModel):
    id: int
    rule_id: int
    timestamp: datetime
    value: float

    class Config:
        from_attributes = True
