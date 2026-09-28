"""The Log Query Language endpoint, and the size helpers the sources listing shares.

Split out of `api.main`, which held eleven unrelated concerns in 1,500 lines:
every parallel feature branch touched that one file and every one of them
conflicted. A pure move — no handler below is changed, and the routes are the
same paths at the same methods.
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel, Field

from denoiser import runtime
from denoiser.api.auth import (
    require_role,
)
from denoiser.api.pagination import MAX_PAGE_SIZE
from denoiser.logging import get_logger
from denoiser.storage.db import User

logger = get_logger(__name__)

router = APIRouter(tags=["Query"])


# ─── SLO / SLI ENDPOINTS ──────────────────────────────────────────────────────────




class LogQuery(BaseModel):
    query: str
    limit: int = Field(100, ge=1, le=MAX_PAGE_SIZE)
    from_ts: int | None = None
    to_ts: int | None = None

@router.post("/v1/logs/query")
def query_logs_api(payload: LogQuery, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """Execute a Log Query Language (LQL) search against the unified ClickHouse log stream."""
    from denoiser.query.parser import QueryTooComplex

    try:
        results = runtime.clickhouse_store().query_logs(
            payload.query,
            limit=payload.limit,
            tenant_id=current_user.tenant_id,
            from_ts=payload.from_ts,
            to_ts=payload.to_ts
        )
        return {"status": "success", "count": len(results), "results": results}
    except QueryTooComplex as e:
        # A query the parser refuses is a bad request, not a server fault; it
        # previously surfaced as an opaque 500 with nothing actionable in it.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"LQL Query failed: {e}")
        raise HTTPException(status_code=500, detail="Query execution failed")
