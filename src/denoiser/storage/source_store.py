"""Shared storage for uploaded log sources.

An upload landed in `data/tenants/{tenant_id}/` on the filesystem of whichever
pod happened to serve the request. With one replica that is invisible. With
two, it is a coin flip: the user uploads through pod A, then `/sources` routed
to pod B does not list the file, and `/analyze` on pod B reports it missing.
Together with the raw-log copy (see `raw_log_sink`) this is the second half of
what pinned the API to a single replica.

The fix keeps the filesystem, and demotes it to a **cache**:

* On upload, the file is written locally *and* mirrored to object storage.
* On resolve, a file missing from local disk is hydrated from object storage
  before the caller sees it, so a pod that never handled the upload can still
  read it.
* On list, the local directory and the bucket are unioned.
* On delete, both copies go.

The analysis pipeline keeps receiving a `pathlib.Path`, so nothing downstream
changes. That matters more than it sounds: the confinement rules in
`denoiser.api.sources` — resolve-then-check, tenant directory containment,
symlink escape refusal — are security-critical and are left exactly as they
were. This module only ensures the bytes are present before those rules run.

With no bucket configured the store is a no-op and behaviour is identical to
before, which keeps a single-node install working with no object storage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from denoiser.logging import get_logger

logger = get_logger(__name__)

#: Bucket prefix for uploaded sources. Distinct from `archive/` (retention) and
#: `raw/` (the ingest copy) so a lifecycle rule can treat the three differently:
#: uploads are user-created input and must not be expired on a log schedule.
SOURCE_PREFIX = "sources"


def _tenant_key(tenant_id: Any) -> str:
    """Tenant id as a safe key segment. Mirrors `sources.tenant_dir`."""
    text = str(tenant_id if tenant_id is not None else "unassigned")
    safe = "".join(ch for ch in text if ch.isalnum() or ch in "-_")
    return safe or "unassigned"


@dataclass(frozen=True)
class RemoteSource:
    """A source that exists in the bucket, whether or not it is cached locally."""

    name: str
    size_bytes: int
    modified: float


class SourceStore(Protocol):
    """Shared home for a tenant's uploaded sources."""

    def enabled(self) -> bool: ...
    def put(self, tenant_id: Any, name: str, local_path: Path) -> None: ...
    def list(self, tenant_id: Any) -> list[RemoteSource]: ...
    def fetch(self, tenant_id: Any, name: str, dest: Path) -> bool: ...
    def delete(self, tenant_id: Any, name: str) -> None: ...


class NullSourceStore:
    """No shared storage. Local disk is the only copy — the previous behaviour."""

    def enabled(self) -> bool:
        return False

    def put(self, tenant_id: Any, name: str, local_path: Path) -> None:
        return None

    def list(self, tenant_id: Any) -> list[RemoteSource]:
        return []

    def fetch(self, tenant_id: Any, name: str, dest: Path) -> bool:
        return False

    def delete(self, tenant_id: Any, name: str) -> None:
        return None


class ObjectSourceStore:
    """Object storage, with the local tenant directory acting as a read cache."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def enabled(self) -> bool:
        return True

    def _key(self, tenant_id: Any, name: str) -> str:
        return f"{SOURCE_PREFIX}/tenant={_tenant_key(tenant_id)}/{os.path.basename(name)}"

    def put(self, tenant_id: Any, name: str, local_path: Path) -> None:
        """Mirror an upload. Raises, because a silent failure here would leave a
        file visible on one pod and absent on every other — the exact confusion
        this module exists to remove."""
        with open(local_path, "rb") as handle:
            self._client.upload_fileobj(handle, self._bucket, self._key(tenant_id, name))

    def list(self, tenant_id: Any) -> list[RemoteSource]:
        prefix = f"{SOURCE_PREFIX}/tenant={_tenant_key(tenant_id)}/"
        found: list[RemoteSource] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []) or []:
                    name = obj["Key"][len(prefix):]
                    if not name or "/" in name:
                        continue
                    modified = obj.get("LastModified")
                    found.append(
                        RemoteSource(
                            name=name,
                            size_bytes=int(obj.get("Size", 0)),
                            modified=modified.timestamp() if modified else 0.0,
                        )
                    )
        except Exception as exc:
            # Degrade to whatever is cached locally rather than failing the
            # listing outright: a partial list is more useful than a 500.
            logger.warning("Source store listing failed for tenant %s: %s", tenant_id, exc)
        return found

    def fetch(self, tenant_id: Any, name: str, dest: Path) -> bool:
        """Hydrate the local cache. Returns True when `dest` now exists."""
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Download to a per-process temporary name and rename into place, so
            # a concurrent reader never observes a half-written file.
            staging = dest.with_name(f".{dest.name}.{os.getpid()}.part")
            with open(staging, "wb") as handle:
                self._client.download_fileobj(self._bucket, self._key(tenant_id, name), handle)
            staging.replace(dest)
            return True
        except Exception as exc:
            logger.debug("Source %s not hydrated for tenant %s: %s", name, tenant_id, exc)
            with_staging = dest.with_name(f".{dest.name}.{os.getpid()}.part")
            with_staging.unlink(missing_ok=True)
            return False

    def delete(self, tenant_id: Any, name: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=self._key(tenant_id, name))
        except Exception as exc:
            logger.warning("Source %s not deleted from the bucket: %s", name, exc)


def _configured_bucket(settings: dict[str, Any]) -> str:
    """The bucket for uploads, or "" when shared storage is not configured.

    Deliberately *not* inherited from the `s3_bucket` archive setting, which
    carries a default value (`semanticos-logs`) on every install. Reading it
    here would switch every deployment onto object storage the moment this
    module shipped — including the ones with no object store running, whose
    uploads would then fail. Shared storage is opt-in.
    """
    return str(os.getenv("SOURCE_BUCKET") or settings.get("source_bucket") or "").strip()


def build_source_store(settings: dict[str, Any] | None = None) -> SourceStore:
    """Object storage when a bucket is configured, otherwise local-disk only.

    `SEMANTICOS_MULTI_REPLICA=1` makes the absence of a bucket an error: on a
    multi-replica deployment, local-only uploads are not a degraded mode, they
    are a bug that presents as files randomly disappearing.
    """
    if settings is None:
        from denoiser.api.platform_settings import load_settings

        settings = load_settings()

    bucket = _configured_bucket(settings)
    if bucket:
        try:
            from denoiser.storage.archiver import S3ArchiverEngine

            return ObjectSourceStore(S3ArchiverEngine.get_s3_client(settings), bucket)
        except Exception as exc:
            logger.error("Source object store unavailable: %s", exc)

    if os.getenv("SEMANTICOS_MULTI_REPLICA", "").lower() in ("1", "true", "yes"):
        raise RuntimeError(
            "SEMANTICOS_MULTI_REPLICA is set but no source bucket is configured. "
            "Set SOURCE_BUCKET (or the s3_bucket platform setting): uploads held "
            "only on one pod's disk are invisible to every other replica."
        )

    return NullSourceStore()
