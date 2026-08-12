"""Uploaded log sources: list, upload, delete.

Split out of `api.main`, which held eleven unrelated concerns in 1,500 lines:
every parallel feature branch touched that one file and every one of them
conflicted. A pure move — no handler below is changed, and the routes are the
same paths at the same methods.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.concurrency import run_in_threadpool

from denoiser import runtime
from denoiser.api import sources as source_registry
from denoiser.api.auth import (
    require_role,
)
from denoiser.logging import get_logger
from denoiser.storage.db import User

logger = get_logger(__name__)

router = APIRouter(tags=["Sources"])


def _human_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _estimate_lines(path: Path, size_bytes: int) -> int:
    """Estimate line count without reading the whole file."""
    if size_bytes == 0:
        return 0
    try:
        with open(path) as f:
            sample = f.read(min(8192, size_bytes))
            lines_in_sample = sample.count("\n")
            if lines_in_sample == 0:
                return 1
            avg_line_len = len(sample) / lines_in_sample
            return int(size_bytes / avg_line_len)
    except Exception:
        return 0



# ─── SOURCES — Dynamic file discovery + upload ───────────────────────────────

#: Largest single log file accepted by upload. Bounded because the previous
#: implementation read the whole body into memory before writing any of it, so
#: one large upload could exhaust the API process.
MAX_UPLOAD_BYTES = int(os.getenv("SEMANTICOS_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
_UPLOAD_CHUNK = 1024 * 1024


@router.get("/sources")
def list_sources(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """List the log files this tenant may analyse."""
    sources = []
    for f in source_registry.list_sources(current_user.tenant_id):
        stat = f.stat()
        sources.append({
            "name": f.name,
            # Relative to the data root: the absolute path told every caller the
            # server's directory layout, and is not something the UI needs.
            "path": str(f.relative_to(Path.cwd())) if f.is_absolute() and str(f).startswith(str(Path.cwd())) else f.name,
            "size_bytes": stat.st_size,
            "size_human": _human_size(stat.st_size),
            "modified": stat.st_mtime,
            "lines_estimate": _estimate_lines(f, stat.st_size),
            "type": "file",
        })
    sources.sort(key=lambda s: s["modified"], reverse=True)
    return sources


@router.post("/sources/upload")
async def upload_source(file: UploadFile = File(...), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    """Upload a log file into this tenant's own source directory."""
    # Collapse to a bare filename: the destination directory is chosen by the
    # server from the authenticated tenant, never by the client.
    safe_name = os.path.basename(file.filename or "")
    if not safe_name or safe_name in (".", "..") or safe_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    dest_dir = source_registry.tenant_dir(current_user.tenant_id)
    dest = (dest_dir / safe_name).resolve()
    if dest.parent != dest_dir.resolve():
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Streamed in fixed chunks and aborted the moment the cap is passed, so an
    # oversized upload costs one chunk of memory rather than its whole size.
    written = 0
    try:
        with open(dest, "wb") as out:
            while chunk := await file.read(_UPLOAD_CHUNK):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds the {_human_size(MAX_UPLOAD_BYTES)} limit",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except OSError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to store upload: {e}")

    # Mirror to shared storage so every replica can see it, not just this pod.
    # A failure here fails the upload: reporting success for a file that only
    # one of N pods can read is worse than reporting the failure, because the
    # user finds out later, as an intermittently missing source.
    store = runtime.source_store()
    if store.enabled():
        try:
            await run_in_threadpool(store.put, current_user.tenant_id, safe_name, dest)
        except Exception as e:
            dest.unlink(missing_ok=True)
            logger.exception("Failed to mirror upload %s to shared storage", safe_name)
            raise HTTPException(
                status_code=503,
                detail=f"Upload could not be stored in shared storage: {e}",
            )

    return {
        "name": safe_name,
        "path": safe_name,
        "size_bytes": written,
        "size_human": _human_size(written),
        "status": "uploaded",
    }


@router.delete("/sources/{filename}")
def delete_source(filename: str, current_user: User = Depends(require_role(["ADMIN"]))):
    """Delete one of this tenant's own uploaded log files."""
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Only ever this tenant's own directory: the shared sample files are not
    # any one tenant's to remove, and another tenant's uploads are not visible.
    # Hydrated first so a delete issued against a replica that never cached the
    # file still finds it, rather than 404-ing on a file the user can see.
    source_registry.hydrate(filename, current_user.tenant_id)

    file_path = (source_registry.tenant_dir(current_user.tenant_id) / filename).resolve()
    if not file_path.is_file() or file_path.parent != source_registry.tenant_dir(current_user.tenant_id).resolve():
        raise HTTPException(status_code=404, detail="File not found or protected")

    try:
        file_path.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Shared copy last: deleting it while the local copy survives would leave
    # the file resurrectable on this pod but gone everywhere else. The other
    # replicas' caches are stale until they next hydrate, which is acceptable —
    # they hold no copy the bucket can restore.
    store = runtime.source_store()
    if store.enabled():
        store.delete(current_user.tenant_id, filename)

    return {"status": "deleted", "filename": filename}


# verify_ingest_auth is now imported from denoiser.api.auth
