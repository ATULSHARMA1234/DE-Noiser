from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

class WidgetSchema(BaseModel):
    id: str
    type: str  # metric_card, time_series, log_table
    title: str
    config: Dict[str, Any]  # e.g., metric_name, query, time_range

class DashboardCreateSchema(BaseModel):
    name: str
    layout: List[Dict[str, Any]] = []
    widgets: List[WidgetSchema] = []
    is_shared: bool = False

class DashboardUpdateSchema(BaseModel):
    name: Optional[str] = None
    layout: Optional[List[Dict[str, Any]]] = None
    widgets: Optional[List[WidgetSchema]] = None
    is_shared: Optional[bool] = None

class DashboardSchema(BaseModel):
    id: int
    name: str
    user_id: int
    layout: List[Dict[str, Any]]
    widgets: List[WidgetSchema]
    is_shared: bool
    created_at: datetime

    class Config:
        from_attributes = True
