from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class SLOCreateSchema(BaseModel):
    name: str
    service: str
    sli_type: str  # e.g., 'availability', 'latency'
    target_percentage: float
    window_days: int = 30

class SLOSchema(BaseModel):
    id: int
    name: str
    service: str
    sli_type: str
    target_percentage: float
    window_days: int
    created_at: datetime

    class Config:
        from_attributes = True

class SLOStatusSchema(BaseModel):
    slo_id: int
    current_value: float
    error_budget_total: int
    error_budget_remaining: int
    burn_rate: float
    status: str  # 'HEALTHY', 'WARNING', 'BREACHED'
    data_points: List[dict]
