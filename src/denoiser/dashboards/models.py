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
    default_time_range: str = "1h"
    template_variables: list[dict[str, Any]] = []

class DashboardUpdateSchema(BaseModel):
    name: str | None = None
    layout: list[dict[str, Any]] | None = None
    widgets: list[WidgetSchema] | None = None
    is_shared: bool | None = None
    default_time_range: str | None = None
    template_variables: list[dict[str, Any]] | None = None

class DashboardSchema(BaseModel):
    id: int
    name: str
    tenant_id: int | None = None
    layout: list[dict[str, Any]]
    widgets: list[WidgetSchema]
    is_shared: bool
    default_time_range: str
    template_variables: list[dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True
