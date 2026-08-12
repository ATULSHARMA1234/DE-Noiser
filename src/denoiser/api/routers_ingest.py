"""The HTTP ingestion endpoint.

Split out of `api.main`, which held eleven unrelated concerns in 1,500 lines:
every parallel feature branch touched that one file and every one of them
conflicted. A pure move — no handler below is changed, and the routes are the
same paths at the same methods.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from fastapi.concurrency import run_in_threadpool

from denoiser import runtime
from denoiser.api.auth import (
    verify_ingest_auth,
)
from denoiser.api.schemas import (
    IngestPayload,
)
from denoiser.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["Ingest"])


@router.post("/ingest")
async def ingest_logs(payload: IngestPayload, tenant_id: int = Depends(verify_ingest_auth)):
    """
    Standard HTTP ingestion endpoint.
    Accepts arrays of JSON logs (standard format from FluentBit / Vector).
    Writes them directly to data/live_stream.log with auto-rotation.
    """
    try:
        body = payload.logs
        if not body:
            raise HTTPException(status_code=400, detail="logs must not be empty")

        # Redact before anything is persisted. Redaction used to run only when
        # building embedding text during analysis, so the raw line — with its
        # SSNs, card numbers, tokens and passwords intact — was what actually
        # got written to disk and to ClickHouse, and what /v1/logs/query handed
        # back. Everything downstream of this point sees redacted content.
        from denoiser.api.platform_settings import (
            raw_log_storage_enabled,
            redact_batch,
        )

        body = redact_batch(body)

        # Serialize each entry once and append the whole batch in a single write.
        serialized = [
            json.dumps(e) if isinstance(e, dict) else str(e)
            for e in body
        ]

        # `store_raw_logs` governs the redundant copy. When it is off, the
        # streaming and search paths still work; only the forensic copy is
        # skipped.
        #
        # The copy goes through the sink rather than straight to a local file:
        # a per-pod `data/live_stream.log` is what forced the API to a single
        # replica. See denoiser.storage.raw_log_sink.
        if raw_log_storage_enabled():
            await run_in_threadpool(runtime.raw_log_sink().write, tenant_id, serialized)

        # Hyperscale ingestion (Phase 24): Push to Redpanda/Kafka instead of ClickHouse directly.
        # send() enqueues without blocking on the broker ack; awaiting the futures
        # together lets aiokafka batch them into few round-trips. The previous
        # send_and_wait per message paid a full round-trip per log — the opposite
        # of hyperscale.
        if runtime.kafka_producer():
            futures = []
            for log_entry in body:
                payload_to_send = log_entry if isinstance(log_entry, dict) else {"raw_text": str(log_entry)}
                payload_to_send["_tenant_id"] = tenant_id
                msg_bytes = json.dumps(payload_to_send).encode('utf-8')
                futures.append(await runtime.kafka_producer().send("logs_topic", msg_bytes))
            if futures:
                await asyncio.gather(*futures)
        else:
            # Fallback to direct ClickHouse insert if Kafka is unavailable.
            # Already redacted above, at the boundary, along with everything
            # else this request fans out to.
            if isinstance(body[0], dict):
                runtime.clickhouse_store().insert_logs(body, tenant_id=tenant_id, redact=False)

        # Task 45: Publish to Redis Pub/Sub for horizontally scaled WebSockets.
        # Pipelined so the fan-out is one round-trip, not one per log.
        try:
            async with runtime.redis_client().pipeline(transaction=False) as pipe:
                for msg in serialized:
                    pipe.publish(f"log_stream:{tenant_id}", msg)
                await pipe.execute()
        except Exception as re:
            logger.warning(f"Failed to publish ingest logs to Redis: {re}")

        return {"status": "success", "ingested": len(body)}
    except Exception as e:
        logger.exception("Ingest failed")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e!s}")
