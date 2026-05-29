from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime

class RunbookCreateSchema(BaseModel):
    name: str
    trigger_condition: Dict[str, Any]
    steps: List[Dict[str, Any]]
    enabled: bool = True

class RunbookSchema(BaseModel):
    id: int
    name: str
    trigger_condition: Dict[str, Any]
    steps: List[Dict[str, Any]]
    enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True

class RunbookExecutionSchema(BaseModel):
    id: int
    runbook_id: int
    incident_id: int
    status: str
    logs: List[str]
    created_at: datetime

    class Config:
        from_attributes = True
