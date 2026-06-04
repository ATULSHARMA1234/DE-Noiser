from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from denoiser.api.auth import require_role
from denoiser.storage.archiver import S3ArchiverEngine
from denoiser.storage.db import User

router = APIRouter(prefix="/storage/archive", tags=["Storage"])


class HydrationRequest(BaseModel):
    archive_filename: str


@router.post("/trigger")
def trigger_archival(current_user: User = Depends(require_role(["ADMIN"]))):
    """
    Manually trigger S3 archival scanning and data tiering.
    Restricted to ADMIN users.
    """
    try:
        S3ArchiverEngine.run_archival()
        return {"status": "success", "message": "Archival processing completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Archival failed: {e}")


@router.post("/hydrate")
def hydrate_archive(payload: HydrationRequest, current_user: User = Depends(require_role(["ADMIN"]))):
    """
    Hydrate and restore logs or traces from S3/local archive into active hot store.
    Restricted to ADMIN users.
    """
    result = S3ArchiverEngine.hydrate_archive(payload.archive_filename)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result
