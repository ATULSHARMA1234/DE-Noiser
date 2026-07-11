from datetime import datetime

from pydantic import BaseModel


class SavedQuerySchema(BaseModel):
    id: int
    name: str
    query_text: str
    user_id: int | None = None
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
    from_ts: int | None = None
    to_ts: int | None = None
    group_by: str | None = None
    file_name: str | None = None  # Scope search to a specific log file
