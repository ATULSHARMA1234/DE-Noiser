from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from denoiser.query.parser import validate_lql


class SavedQuerySchema(BaseModel):
    id: int
    name: str
    query_text: str
    user_id: int | None = None
    created_at: datetime
    last_used: datetime

    model_config = ConfigDict(from_attributes=True)

class QueryCreateSchema(BaseModel):
    name: str
    query_text: str

    _valid_query = field_validator("query_text")(validate_lql)

class QueryRequestSchema(BaseModel):
    query: str
    limit: int = 100
    from_ts: int | None = None
    to_ts: int | None = None
    group_by: str | None = None
    file_name: str | None = None  # Scope search to a specific log file
