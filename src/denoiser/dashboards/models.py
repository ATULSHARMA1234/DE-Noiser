from datetime import datetime
from typing import Any

from pydantic import BaseModel


class WidgetSchema(BaseModel):
    id: str
    type: str  # metric_card, time_series, log_table
    title: str
    config: dict[str, Any]  # e.g., metric_name, query, time_range

class DashboardCreateSchema(BaseModel):
    name: str
    layout: list[dict[str, Any]] = []
    widgets: list[WidgetSchema] = []
    is_shared: bool = False

class DashboardUpdateSchema(BaseModel):
    name: str | None = None
    layout: list[dict[str, Any]] | None = None
    widgets: list[WidgetSchema] | None = None
    is_shared: bool | None = None

class DashboardSchema(BaseModel):
    id: int
    name: str
    tenant_id: int | None = None
    layout: list[dict[str, Any]]
    widgets: list[WidgetSchema]
    is_shared: bool
    created_at: datetime

    class Config:
        from_attributes = True
