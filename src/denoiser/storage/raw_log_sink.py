"""Where the raw, on-disk copy of an ingested batch goes.

Three ingest paths — `/ingest`, the OTLP logs endpoint, and the Elastic/Splunk
compatibility shims — each appended their batch to `data/live_stream.log` with
a plain `open(..., "a")`. That single file is the reason the API could not run
more than one replica: two processes appending to one path need shared,
POSIX-locking storage, and the Helm chart says as much
(`values.yaml`: ">1 replica needs ReadWriteMany"). Putting NFS under a
rotating, continuously-appended log is not a fix, it is a slower fault.

This module makes that copy a sink with two implementations:

* **`ObjectStoreRawLogSink`** — one gzipped JSONL object per ingest batch,
  under `raw/tenant=<id>/dt=<date>/`, with the writing instance in the key. Two
  replicas can never collide, so the API scales horizontally.
* **`LocalFileRawLogSink`** — the previous behaviour, including the 100 MB
  rotation, kept for single-node and development installs where no object store
  is configured.

**What this copy is and is not.** It is a redundant forensic record. The
authoritative path is `ingest -> Kafka -> worker -> ClickHouse`, which is
at-least-once and acknowledged. So a failure to write here is logged and
swallowed rather than failing the request: refusing an ingest because a
secondary copy could not be written would trade a durable pipeline for a
convenience one. It is governed by the `store_raw_logs` platform setting, and
when that is off nothing is written at all.

One object per batch rather than an appended stream is deliberate — object
stores have no append — so a busy deployment produces many small objects.
Configure a bucket lifecycle rule to expire or compact them; the retention
window is a customer policy question, not something to hard-code here.
"""

from __future__ import annotations

import gzip
import io
import os
import secrets
import socket
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from denoiser.logging import get_logger

logger = get_logger(__name__)

#: Identifies the writing process in an object key. Two replicas — and two
#: uvicorn workers inside one replica — must never derive the same key, or one
#: silently overwrites the other's batch.
_INSTANCE_ID = f"{socket.gethostname()}-{os.getpid()}"

#: Bucket prefix for the raw copy. Kept distinct from `archive/`, which the
#: retention job owns and the tenant-purge walks.
RAW_PREFIX = "raw"

#: Rotate the local file past this size, as the inline implementation did.
LOCAL_ROTATE_BYTES = 100 * 1024 * 1024


def _tenant_key(value: Any) -> str:
    """Tenant id as a safe path/key segment. Mirrors the archiver's rule."""
    text = "" if value is None else str(value).strip()
    if not text or not all(c.isalnum() or c in "-_" for c in text):
        return "unknown"
    return text


class RawLogSink(Protocol):
    """Accepts already-serialized log lines for one tenant."""

    def write(self, tenant_id: Any, lines: list[str]) -> None: ...


class LocalFileRawLogSink:
    """Appends to `data/live_stream.log`, rotating past 100 MB.

    Correct for one process. Retained because a single-node install is a real
    deployment shape for this product, and because `query.py`'s ClickHouse-down
    fallback still reads `data/*.log` off local disk.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._lock = threading.Lock()

    def write(self, tenant_id: Any, lines: list[str]) -> None:
        if not lines:
            return
        stream_file = self._data_dir / "live_stream.log"
        # Serialised across threads: FastAPI runs sync routes in a threadpool,
        # and two concurrent rotations would rename the same file twice.
        with self._lock:
            try:
                self._data_dir.mkdir(parents=True, exist_ok=True)
                if stream_file.exists() and stream_file.stat().st_size > LOCAL_ROTATE_BYTES:
                    rotated_name = f"live_stream_{int(datetime.now(UTC).timestamp())}.log"
                    stream_file.rename(self._data_dir / rotated_name)
                    logger.info("Rotated live_stream.log to %s", rotated_name)
                with open(stream_file, "a", encoding="utf-8") as handle:
                    handle.write("".join(f"{line}\n" for line in lines))
            except Exception as exc:
                logger.warning("Raw log copy failed (local file): %s", exc)


class ObjectStoreRawLogSink:
    """One gzipped JSONL object per batch, partitioned by tenant and date.

    The key carries the writing instance and a monotonic counter, so no two
    processes — replicas or uvicorn workers — can produce the same key. That is
    what removes the shared-filesystem requirement.
    """

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket
        self._lock = threading.Lock()
        self._sequence = 0

    def _next_key(self, tenant_id: Any) -> str:
        """A key no other writer can produce.

        Three things keep it unique, and all three are needed. The instance id
        separates replicas and uvicorn workers. The counter separates batches
        written by one sink inside the same second. The random suffix covers
        what neither does — two sink objects constructed in the same process,
        which share an instance id and each start counting at one. Dropping it
        would make uniqueness depend on there being exactly one sink per
        process, which is true today and is not a property worth betting a
        customer's logs on.
        """
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        now = datetime.now(UTC)
        return (
            f"{RAW_PREFIX}/tenant={_tenant_key(tenant_id)}/dt={now:%Y-%m-%d}/"
            f"{now:%H%M%S}-{_INSTANCE_ID}-{sequence}-{secrets.token_hex(4)}.jsonl.gz"
        )

    def write(self, tenant_id: Any, lines: list[str]) -> None:
        if not lines:
            return
        try:
            buffer = io.BytesIO()
            with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
                gz.write("".join(f"{line}\n" for line in lines).encode("utf-8"))
            self._client.put_object(
                Bucket=self._bucket,
                Key=self._next_key(tenant_id),
                Body=buffer.getvalue(),
                ContentType="application/gzip",
            )
        except Exception as exc:
            # Never fails the ingest: Kafka/ClickHouse carry the authoritative
            # record, and this copy is redundant by design.
            logger.warning("Raw log copy failed (object store): %s", exc)


def _configured_bucket(settings: dict[str, Any]) -> str:
    """The bucket for the raw copy, or "" when none is configured.

    Deliberately *not* inherited from the `s3_bucket` archive setting: that one
    has a default value on every install, so reading it would route every
    deployment's ingest through an object store it may not be running.
    """
    return str(os.getenv("RAW_LOG_BUCKET") or settings.get("raw_log_bucket") or "").strip()


def build_raw_log_sink(settings: dict[str, Any] | None = None, data_dir: Path | None = None) -> RawLogSink:
    """Pick a sink from configuration.

    Object storage when a bucket is configured, local file otherwise. The
    fallback is deliberate rather than a hard requirement: a single-node
    evaluation install should not need MinIO before it can accept a log line.

    `SEMANTICOS_MULTI_REPLICA=1` turns the fallback into a refusal, because on a
    multi-replica deployment a local-file copy is not merely degraded — it is
    split across pods and cannot be read back as one stream.
    """
    from denoiser import runtime

    if settings is None:
        from denoiser.api.platform_settings import load_settings

        settings = load_settings()

    bucket = _configured_bucket(settings)
    if bucket:
        try:
            from denoiser.storage.archiver import S3ArchiverEngine

            return ObjectStoreRawLogSink(S3ArchiverEngine.get_s3_client(settings), bucket)
        except Exception as exc:
            logger.error("Raw log object store unavailable, falling back to local file: %s", exc)

    if os.getenv("SEMANTICOS_MULTI_REPLICA", "").lower() in ("1", "true", "yes"):
        raise RuntimeError(
            "SEMANTICOS_MULTI_REPLICA is set but no raw-log bucket is configured. "
            "Set RAW_LOG_BUCKET (or the s3_bucket platform setting), or turn off "
            "raw log storage — a local-file copy cannot be shared between replicas."
        )

    return LocalFileRawLogSink(data_dir or runtime.data_dir())
