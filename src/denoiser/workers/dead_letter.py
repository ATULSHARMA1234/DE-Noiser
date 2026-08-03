"""Durable quarantine for records the ingestion pipeline cannot write.

The consumer already gets the hard part right: manual offset commits, a
backpressure ceiling, a retry budget, and a poison-pill escape so one bad batch
cannot wedge a partition forever. Then it quarantined the casualty by appending
to `data/dlq/ingestion_dlq.jsonl` — a file on the local disk of a worker pod
that the Helm chart gives no volume. On the next restart, deploy or eviction,
the quarantine was a delete.

That is the worst failure mode in the system. For a customer whose logs are a
compliance record, "we dropped it and nobody was told" is strictly worse than
"we stopped accepting writes".

So the DLQ is written where the rest of the pipeline's durability already
lives:

1. **A Kafka topic** (`ingestion_dlq` by default). Replicated, survives any pod,
   and replayable with the same tooling as the main topics.
2. **The local file**, only when Kafka cannot be reached — which is the one
   situation where the broker is not an option. Logged at error, because it is
   a fallback with a known loss window, not an equivalent.

Every dead-letter also increments a Redis counter so the depth is visible to
`/internal/metrics` and can be alerted on. Silent data loss with no signal is
the part that makes it dangerous; a number that goes up is a page.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from denoiser.logging import get_logger

logger = get_logger(__name__)

#: Kafka topic holding quarantined records.
DLQ_TOPIC = os.getenv("SEMANTICOS_DLQ_TOPIC", "ingestion_dlq")

#: Local fallback, used only when the broker is unreachable.
DLQ_PATH = Path(os.getenv("SEMANTICOS_DLQ_PATH", "data/dlq/ingestion_dlq.jsonl"))

#: Redis counter of everything ever quarantined by this deployment. A counter
#: rather than a gauge: the interesting signal is the rate of increase, and a
#: gauge would need someone to decrement it on replay.
DLQ_COUNTER_KEY = "semanticos:ingestion:dlq_total"

#: Records the source topic separately, so an alert can say *what* is failing.
DLQ_COUNTER_BY_TOPIC_KEY = "semanticos:ingestion:dlq_by_topic"


def _jsonable(payload: Any) -> Any:
    if isinstance(payload, (dict, list, str, int, float, bool, type(None))):
        return payload
    return str(payload)


def build_record(topic: str, reason: str, payload: Any) -> dict[str, Any]:
    """The quarantine envelope. Carries enough to diagnose and to replay."""
    return {
        "dead_lettered_at": datetime.now(UTC).isoformat(),
        "topic": topic,
        "reason": reason,
        "payload": _jsonable(payload),
    }


def write_local(record: dict[str, Any]) -> None:
    """Append to the local fallback file. Never raises."""
    try:
        DLQ_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(DLQ_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:
        logger.error("Failed to write to the local DLQ file (%s): %s", record.get("topic"), exc)


async def _count(redis_client: Any, topic: str) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.incr(DLQ_COUNTER_KEY)
        await redis_client.hincrby(DLQ_COUNTER_BY_TOPIC_KEY, topic, 1)
    except Exception as exc:
        # The counter is observability, not the record itself. Losing it must
        # not lose the quarantine.
        logger.warning("Could not increment the DLQ counter: %s", exc)


async def dead_letter(
    topic: str,
    reason: str,
    payload: Any,
    *,
    producer: Any = None,
    redis_client: Any = None,
) -> None:
    """Quarantine one record. Never raises.

    `producer` is the worker's Kafka producer. When it is absent or the send
    fails, the record goes to the local file so it is not lost outright — the
    caller has already decided this record cannot be written, and raising here
    would take down the consumer over a record it was in the middle of
    discarding.
    """
    record = build_record(topic, reason, payload)

    delivered = False
    if producer is not None:
        try:
            await producer.send_and_wait(
                DLQ_TOPIC, json.dumps(record, default=str).encode("utf-8")
            )
            delivered = True
        except Exception as exc:
            logger.error(
                "DLQ topic %s unreachable, falling back to the local file "
                "(this copy does not survive a pod restart): %s",
                DLQ_TOPIC,
                exc,
            )

    if not delivered:
        write_local(record)

    await _count(redis_client, topic)


async def read_counters(redis_client: Any) -> dict[str, Any]:
    """Total and per-topic dead-letter counts, for the metrics endpoint."""
    try:
        total = await redis_client.get(DLQ_COUNTER_KEY)
        by_topic = await redis_client.hgetall(DLQ_COUNTER_BY_TOPIC_KEY)
    except Exception as exc:
        logger.warning("Could not read DLQ counters: %s", exc)
        return {"total": 0, "by_topic": {}}

    return {
        "total": int(total or 0),
        "by_topic": {str(k): int(v) for k, v in (by_topic or {}).items()},
    }
