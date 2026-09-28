"""The analysis entry point and run status.

Split out of `api.main`, which held eleven unrelated concerns in 1,500 lines:
every parallel feature branch touched that one file and every one of them
conflicted. A pure move — no handler below is changed, and the routes are the
same paths at the same methods.
"""

from __future__ import annotations

import os

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from denoiser.api import sources as source_registry
from denoiser.api.auth import (
    require_role,
)
from denoiser.api.schemas import (
    AnalysisRequest,
)
from denoiser.logging import get_logger
from denoiser.storage.db import User

logger = get_logger(__name__)

router = APIRouter(tags=["Analyze"])


# ─── ANALYZE — Core analysis engine ──────────────────────────────────────────

@router.post("/analyze")
async def run_analysis(request: AnalysisRequest, current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    """
    Submit analysis to the Phase 4 Celery queue.

    Local development remains usable without Redis: if the broker cannot be
    reached, the same task body runs synchronously and returns the completed
    analysis response.
    """
    from kombu.exceptions import OperationalError

    from denoiser.workers.analysis_worker import run_analysis_task

    payload = request.model_dump()
    # Scope the resulting run/incidents to the requesting user's tenant so they
    # show up in the tenant-filtered /runs and /incidents views.
    payload["tenant_id"] = current_user.tenant_id

    # Resolve the sources here as well as in the worker. The worker is the
    # security boundary — it is what opens the file — but rejecting only there
    # means the caller gets "queued" for a request that was never going to run,
    # and has to poll a task id to discover a mistake the API already knew
    # about.
    #
    # A run with *some* readable sources still proceeds, matching the existing
    # multi-source contract: one unreachable service's log should not discard
    # the others. Only a request with nothing readable is refused outright.
    requested = list(request.sources or ([request.source] if request.source else []))
    resolution_errors: list[str] = []
    for src in requested:
        try:
            source_registry.resolve_source(str(src), current_user.tenant_id)
        except source_registry.SourceNotAllowed as e:
            resolution_errors.append(str(e))

    if requested and len(resolution_errors) == len(requested):
        # 404 rather than 400: resolve_source deliberately gives the same answer
        # for "outside the data root", "another tenant's file" and "no such
        # file", so that the endpoint cannot be used to probe for either. A
        # single not-found is the honest status for that single message.
        raise HTTPException(status_code=404, detail=resolution_errors[0])

    # If running inside pytest, force synchronous execution for test compatibility
    if "PYTEST_CURRENT_TEST" in os.environ:
        result = run_analysis_task.apply(args=[payload])
        if result.failed():
            raise HTTPException(status_code=500, detail=str(result.result))
        res_data = result.result
        if isinstance(res_data, dict) and res_data.get("status") == "error":
            raise HTTPException(status_code=404, detail=res_data.get("message"))
        return res_data

    try:
        async_result = run_analysis_task.delay(payload)
        return {"status": "queued", "task_id": async_result.id}
    except OperationalError as e:
        logger.warning(f"Celery broker unavailable; running analysis inline: {e}")
        result = run_analysis_task.apply(args=[payload])
        if result.failed():
            raise HTTPException(status_code=500, detail=str(result.result))
        return result.result


@router.get("/tasks/{task_id}")
def get_task_status(task_id: str, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """Return Celery task state and the final analysis payload when complete."""
    from celery.result import AsyncResult

    from denoiser.workers.analysis_worker import celery_app

    result = AsyncResult(task_id, app=celery_app)
    response = {"task_id": task_id, "status": result.status}

    if result.status == "PROGRESS":
        response["meta"] = result.info or {}
    elif result.status == "SUCCESS":
        response["result"] = result.result
    elif result.status == "FAILURE":
        response["error"] = str(result.result)

    return response
