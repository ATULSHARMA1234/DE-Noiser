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
        auto_offset_reset="earliest"
    )

    await consumer.start()
    logger.info("Connected to Redpanda/Kafka.")

    try:
        # Size-or-time batching. Flushing only at BATCH_SIZE would strand any
        # remainder (< BATCH_SIZE) in memory indefinitely — those logs would never
        # reach ClickHouse until the process shut down. The linger deadline makes
        # sure a partial batch is still written promptly.
        batch_logs = []
        batch_traces = []
        BATCH_SIZE = 1000
        LINGER_SECONDS = 2.0
        last_flush = time.monotonic()

        while True:
            # Wait up to LINGER for more records, then flush whatever we have.
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

            due = (time.monotonic() - last_flush) >= LINGER_SECONDS

            if batch_logs and (len(batch_logs) >= BATCH_SIZE or due):
                _flush_logs(ch_store, batch_logs)
                batch_logs = []
                last_flush = time.monotonic()

            if batch_traces and (len(batch_traces) >= BATCH_SIZE or due):
                _flush_traces(batch_traces)
                batch_traces = []
                last_flush = time.monotonic()

    finally:
        # Flush remaining
        if batch_logs:
            _flush_logs(ch_store, batch_logs)
        if batch_traces:
            _flush_traces(batch_traces)
        await consumer.stop()

def _flush_logs(ch_store, batch_logs):
    # Group by tenant_id
    by_tenant = {}
    for log, t_id in batch_logs:
        if t_id not in by_tenant:
            by_tenant[t_id] = []
        by_tenant[t_id].append(log)

    for t_id, logs in by_tenant.items():
        try:
            ch_store.insert_logs(logs, tenant_id=t_id)
            logger.info(f"Flushed {len(logs)} logs to ClickHouse for tenant {t_id}")
        except Exception as e:
            logger.error(f"Failed to flush logs for tenant {t_id}: {e}")

def _flush_traces(batch_traces):
    db = SessionLocal()
    try:
        for trace, t_id in batch_traces:
            try:
                process_otlp_traces(db, trace, tenant_id=t_id)
            except Exception as e:
                logger.error(f"Failed to process trace for tenant {t_id}: {e}")
    finally:
        db.close()
        logger.info(f"Flushed {len(batch_traces)} traces")

if __name__ == "__main__":
    asyncio.run(run_ingestion_worker())
