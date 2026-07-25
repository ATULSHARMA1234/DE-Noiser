from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SpanEvent(BaseModel):
    name: str
    timestamp: datetime
    attributes: dict[str, Any] = Field(default_factory=dict)

class SpanSchema(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    service_name: str
    operation_name: str
    start_time: datetime
    end_time: datetime
    duration_ms: float
    status_code: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[SpanEvent] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

class TraceSchema(BaseModel):
    trace_id: str
    root_service: str
    root_operation: str
    start_time: datetime
    duration_ms: float
    span_count: int
    error_count: int
    spans: list[SpanSchema] = Field(default_factory=list)
