"""
Distributed lock for scheduled jobs.

The retention jobs are destructive: they gzip log files, upload them, delete the
local copies, and delete database rows. APScheduler runs in-process, so with N
API replicas every one of them fires the same nightly job at the same minute —
N processes racing to archive and delete the same files. That is fine at one
replica and silently corrupting at three, which is exactly the point where a
deployment starts to matter.

Each job occurrence takes a Redis lock keyed by job id and occurrence, so one
replica wins and the rest skip. The lock is per *occurrence*, not per job, so a
missed night does not block the next one.

Availability policy, deliberately asymmetric:

  no REDIS_URL configured   -> single-instance deployment, run without a lock
  REDIS_URL set but down    -> cannot prove exclusivity, skip and log an error

Skipping delays archival by a day, which is recoverable. Running without a lock
in a cluster races destructive deletes, which is not.
"""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime

from denoiser.logging import get_logger

logger = get_logger(__name__)

# Long enough that a slow archival keeps its claim, short enough that a replica
# killed mid-job frees the lock well before the next occurrence.
DEFAULT_LOCK_TTL_SECONDS = 3600

_INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}"


def _redis_url() -> str | None:
    """None when no lock backend is configured (treated as single-instance)."""
    return os.getenv("REDIS_URL") or None


def _make_client(url: str):
    """Build the lock client. Seam for tests; the real one talks to Redis."""
    import redis.asyncio as redis_asyncio

    return redis_asyncio.from_url(url, decode_responses=True)


def _occurrence_key(job_id: str, now: datetime) -> str:
    """One key per job per day. Daily jobs, so day granularity is the occurrence."""
    return f"semanticos:joblock:{job_id}:{now.strftime('%Y-%m-%d')}"


async def acquire_job_slot(
    job_id: str,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    now: datetime | None = None,
) -> bool:
    """True if this process may run ``job_id`` for the current occurrence."""
    url = _redis_url()
    if url is None:
        logger.debug(f"No REDIS_URL; running {job_id} unlocked (single-instance)")
        return True

    key = _occurrence_key(job_id, now or datetime.now(UTC))

    try:
        client = _make_client(url)
        try:
            # SET NX EX is atomic: exactly one replica gets True.
            won = await client.set(key, _INSTANCE_ID, nx=True, ex=ttl_seconds)
        finally:
            await client.aclose()
    except Exception as e:
        # Configured but unreachable — exclusivity cannot be established.
        logger.error(f"Skipping {job_id}: lock backend unreachable ({e})")
        return False

    if won:
        logger.info(f"Acquired job slot {key} as {_INSTANCE_ID}")
        return True

    logger.info(f"Skipping {job_id}: another instance holds {key}")
    return False


def single_instance(job_id: str, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS):
    """Decorator running an async job only on the replica that wins the lock."""

    def decorator(fn):
        async def wrapper(*args, **kwargs):
            if not await acquire_job_slot(job_id, ttl_seconds):
                return None
            return await fn(*args, **kwargs)

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__wrapped__ = fn
        return wrapper

    return decorator
