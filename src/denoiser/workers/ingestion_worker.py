import asyncio
import json
import os
import time

from aiokafka import AIOKafkaConsumer

from denoiser.logging import get_logger
from denoiser.storage.clickhouse_store import ClickHouseStore
from denoiser.storage.db import SessionLocal
from denoiser.tracing.otlp_collector import process_otlp_traces

logger = get_logger(__name__)

async def run_ingestion_worker():
    logger.info("Starting Hyperscale Ingestion Worker (Kafka Consumer)...")
    kafka_broker = os.getenv("KAFKA_BROKER", "localhost:9092")

    # Initialize ClickHouse Store
    ch_store = ClickHouseStore()

    consumer = AIOKafkaConsumer(
        "logs_topic",
        "traces_topic",
        bootstrap_servers=kafka_broker,
        group_id="semanticos-ingestion-group",
        auto_offset_reset="earliest",
        # Commit only after a batch is durably written. With auto-commit the
        # offsets advance on a timer regardless of whether ClickHouse accepted
        # the rows, so any write failure silently loses those records forever.
        enable_auto_commit=False,
    )

    await consumer.start()
    logger.info("Connected to Redpanda/Kafka.")

    try:
        # Size-or-time batching. Flushing only at BATCH_SIZE would strand any
        # remainder (< BATCH_SIZE) in memory indefinitely. Each topic keeps its
        # own linger deadline: a shared deadline lets a hot topic (which keeps
        # resetting it by flushing on size) starve a quiet one indefinitely.
        batch_logs = []
        batch_traces = []
        BATCH_SIZE = 1000
        LINGER_SECONDS = 2.0
        now = time.monotonic()
        last_logs_flush = now
        last_traces_flush = now

        while True:
            records = await consumer.getmany(timeout_ms=int(LINGER_SECONDS * 1000))

            for _tp, msgs in records.items():
                for msg in msgs:
                    try:
                        payload = json.loads(msg.value.decode("utf-8"))
                        tenant_id = payload.pop("_tenant_id", "default_tenant")
                        if msg.topic == "logs_topic":
                            batch_logs.append((payload, tenant_id))
                        elif msg.topic == "traces_topic":
                            batch_traces.append((payload, tenant_id))
                    except Exception as e:
                        logger.error(f"Failed to process message from {msg.topic}: {e}")

            now = time.monotonic()
            logs_due = batch_logs and (
                len(batch_logs) >= BATCH_SIZE or (now - last_logs_flush) >= LINGER_SECONDS
            )
            traces_due = batch_traces and (
                len(batch_traces) >= BATCH_SIZE or (now - last_traces_flush) >= LINGER_SECONDS
            )

            ok = True
            if logs_due:
                ok &= _flush_logs(ch_store, batch_logs)
                batch_logs = []
                last_logs_flush = time.monotonic()

            if traces_due:
                ok &= _flush_traces(batch_traces)
                batch_traces = []
                last_traces_flush = time.monotonic()

            # Advance offsets only once everything we consumed is safely stored.
            # On failure we leave the offsets where they are and let the batch be
            # redelivered rather than acknowledging data we dropped.
            if (logs_due or traces_due) and ok:
                await consumer.commit()

    finally:
        # Flush remaining
        if batch_logs:
            _flush_logs(ch_store, batch_logs)
        if batch_traces:
            _flush_traces(batch_traces)
        await consumer.stop()

def _flush_logs(ch_store, batch_logs) -> bool:
    """Write a batch to ClickHouse. Returns True only if every tenant's rows landed."""
    # Group by tenant_id
    by_tenant = {}
    for log, t_id in batch_logs:
        if t_id not in by_tenant:
            by_tenant[t_id] = []
        by_tenant[t_id].append(log)

    ok = True
    for t_id, logs in by_tenant.items():
        try:
            # insert_logs returns False (rather than raising) when the client is
            # unavailable or the insert fails -- treat that as a failure too.
            if ch_store.insert_logs(logs, tenant_id=t_id):
                logger.info(f"Flushed {len(logs)} logs to ClickHouse for tenant {t_id}")
            else:
                ok = False
                logger.error(f"ClickHouse rejected {len(logs)} logs for tenant {t_id}")
        except Exception as e:
            ok = False
            logger.error(f"Failed to flush logs for tenant {t_id}: {e}")
    return ok

def _flush_traces(batch_traces) -> bool:
    """Persist a batch of traces. Returns True only if every trace was stored."""
    db = SessionLocal()
    failed = 0
    try:
        for trace, t_id in batch_traces:
            try:
                process_otlp_traces(db, trace, tenant_id=t_id)
            except Exception as e:
                failed += 1
                logger.error(f"Failed to process trace for tenant {t_id}: {e}")
    finally:
        db.close()

    stored = len(batch_traces) - failed
    if failed:
        # Previously this always logged success, so a batch that dropped every
        # trace still reported "Flushed N traces".
        logger.error(f"Flushed {stored}/{len(batch_traces)} traces ({failed} failed)")
    else:
        logger.info(f"Flushed {stored} traces")
    return failed == 0

if __name__ == "__main__":
    asyncio.run(run_ingestion_worker())
