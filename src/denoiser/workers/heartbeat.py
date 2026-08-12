"""Liveness signal from the ingestion consumer.

``POST /ingest`` returns 200 as soon as a record is handed to Kafka. If the
ingestion consumer is not running, the topic fills up and nothing ever reaches
ClickHouse — while readiness stayed green, because it only ever checked the
*producer*. That is a silent data-loss window: successful writes, a healthy
health check, and no logs to query.

The consumer publishes a heartbeat here on every cycle. The API reads it during
readiness, so an absent or stalled consumer takes the instance out of rotation
instead of accepting writes that go nowhere.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from denoiser.logging import get_logger

logger = get_logger(__name__)

HEARTBEAT_KEY = "semanticos:ingestion:consumer"

# How often the consumer refreshes its heartbeat. Well under the stale window so
# an ordinary slow flush cycle never looks like an outage.
HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("INGESTION_HEARTBEAT_INTERVAL", "5"))


def _stale_after() -> float:
    """Age at which a heartbeat means "the consumer is gone", not "it is busy".

    Read per call rather than captured at import so tests and operators can
    change it without reloading the module.
    """
    return float(os.getenv("INGESTION_HEARTBEAT_STALE_SECONDS", "60"))


def _lag_ceiling() -> int:
    """Backlog above which the consumer is losing ground rather than lagging."""
    return int(os.getenv("INGESTION_LAG_CRITICAL", "500000"))


async def publish_heartbeat(redis_client: Any, *, lag: int | None, assigned: int) -> None:
    """Record that the consumer is alive and how far behind it is.

    Never raises: a Redis blip must not take down ingestion, which is the one
    thing that is definitely still working at that moment.
    """
    payload = {
        "at": time.time(),
        "lag": lag,
        "assigned_partitions": assigned,
        "pid": os.getpid(),
    }
    try:
        # Expire a little after the stale window: a key that vanishes and a key
        # that is too old are the same verdict, so the TTL is a backstop.
        await redis_client.set(
            HEARTBEAT_KEY, json.dumps(payload), ex=int(_stale_after() * 2)
        )
    except Exception as e:
        logger.warning(f"Could not publish ingestion heartbeat: {e}")


async def read_heartbeat(redis_client: Any) -> dict[str, Any] | None:
    """Fetch the consumer's last heartbeat, or None if it never checked in."""
    try:
        raw = await redis_client.get(HEARTBEAT_KEY)
    except Exception as e:
        logger.warning(f"Could not read ingestion heartbeat: {e}")
        return None
    if not raw:
        return None
    try:
        decoded: dict[str, Any] = json.loads(
            raw if isinstance(raw, str) else raw.decode("utf-8")
        )
        return decoded
    except (ValueError, AttributeError):
        return None


def evaluate_heartbeat(
    heartbeat: dict[str, Any] | None, *, now: float | None = None
) -> tuple[bool, str]:
    """Turn a heartbeat into a readiness verdict.

    Returns ``(healthy, detail)``. Unhealthy means log ingestion is accepting
    writes that will not be queryable, which is worth failing readiness over.
    """
    if heartbeat is None:
        return False, "error: no ingestion consumer has checked in (is the worker running?)"

    age = (now if now is not None else time.time()) - float(heartbeat.get("at", 0))
    if age > _stale_after():
        return False, f"error: ingestion consumer heartbeat is {int(age)}s old (stalled or stopped)"

    lag = heartbeat.get("lag")
    if isinstance(lag, int) and lag > _lag_ceiling():
        return False, f"error: ingestion consumer is {lag} records behind"

    if isinstance(lag, int):
        return True, f"ok (lag {lag})"
    return True, "ok"
