from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class SavedQuerySchema(BaseModel):
    id: int
    name: str
    query_text: str
    user_id: Optional[int] = None
    created_at: datetime
    last_used: datetime

    class Config:
        from_attributes = True

class QueryCreateSchema(BaseModel):
    name: str
    query_text: str

class QueryRequestSchema(BaseModel):
    query: str
    limit: int = 100
