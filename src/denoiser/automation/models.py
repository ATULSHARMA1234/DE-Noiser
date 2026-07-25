from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class RunbookCreateSchema(BaseModel):
    name: str
    trigger_condition: dict[str, Any]
    steps: list[dict[str, Any]]
    enabled: bool = True

class RunbookSchema(BaseModel):
    id: int
    name: str
    trigger_condition: dict[str, Any]
    steps: list[dict[str, Any]]
    enabled: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class RunbookExecutionSchema(BaseModel):
    id: int
    runbook_id: int
    incident_id: int
    status: str
    logs: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
