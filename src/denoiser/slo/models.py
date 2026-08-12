from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SLOCreateSchema(BaseModel):
    name: str
    service: str
    sli_type: str  # e.g., 'availability', 'latency'
    target_percentage: float
    window_days: int = 30
    # Objective for a latency SLI, in milliseconds. Ignored for other SLI types.
    latency_threshold_ms: float = Field(default=500.0, gt=0, le=600_000)

class SLOSchema(BaseModel):
    id: int
    name: str
    service: str
    sli_type: str
    target_percentage: float
    window_days: int
    latency_threshold_ms: float | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class SLOStatusSchema(BaseModel):
    slo_id: int
    current_value: float
    # Events in the window, and the subset the SLI could actually be measured
    # over. For a latency SLO these differ: only log lines carrying a duration
    # are part of the objective.
    total_events: int = 0
    measured_events: int = 0
    good_events: int = 0
    error_budget_total: int
    error_budget_remaining: int
    burn_rate: float
    # 'HEALTHY', 'WARNING', 'BREACHED', or 'NO_DATA' when nothing in the window
    # could be measured — which is not the same as passing.
    status: str
    data_points: list[dict]
    threshold_ms: float | None = None
