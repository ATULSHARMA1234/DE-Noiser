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
        # At-least-once delivery.
        #
        # Each topic keeps its own batch, its own linger deadline (a shared
        # deadline lets a hot topic, which keeps resetting it by flushing on
        # size, starve a quiet one), and its own pending offsets.
        #
        # A batch is only dropped from memory once ClickHouse has accepted it,
        # and offsets are committed per-partition for exactly what was written.
        # Clearing a failed batch would lose it for good: not committing does not
        # rewind the consumer's fetch position, so those records would never be
        # redelivered. On failure we keep the batch and retry.
        BATCH_SIZE = 1000
        LINGER_SECONDS = 2.0
        MAX_BATCH = BATCH_SIZE * 10  # backpressure ceiling while retrying

        # topic -> {"batch": [...], "offsets": {TopicPartition: last_offset}, "last": monotonic, "fails": int}
        state = {
            "logs_topic": {"batch": [], "offsets": {}, "last": time.monotonic(), "fails": 0},
            "traces_topic": {"batch": [], "offsets": {}, "last": time.monotonic(), "fails": 0},
        }
        flushers = {"logs_topic": lambda b: _flush_logs(ch_store, b), "traces_topic": _flush_traces}

        while True:
            records = await consumer.getmany(timeout_ms=int(LINGER_SECONDS * 1000))

            for tp, msgs in records.items():
                st = state.get(tp.topic)
                if st is None:
                    continue
                for msg in msgs:
                    try:
                        payload = json.loads(msg.value.decode("utf-8"))
                        tenant_id = payload.pop("_tenant_id", "default_tenant")
                        st["batch"].append((payload, tenant_id))
                    except Exception as e:
                        # A malformed message must not stall the partition; skip it
                        # but still acknowledge it so we don't spin on it forever.
                        logger.error(f"Skipping unparseable message from {tp.topic}: {e}")
                    # Track the highest offset we've taken responsibility for.
                    st["offsets"][tp] = max(st["offsets"].get(tp, -1), msg.offset)

            for topic, st in state.items():
                if not st["batch"]:
                    continue
                now = time.monotonic()
                due = len(st["batch"]) >= BATCH_SIZE or (now - st["last"]) >= LINGER_SECONDS
                if not due:
                    continue

                if flushers[topic](st["batch"]):
                    # Durably written -- commit exactly these partitions/offsets.
                    if st["offsets"]:
                        await consumer.commit(
                            {tp: off + 1 for tp, off in st["offsets"].items()}
                        )
                    st["batch"] = []
                    st["offsets"] = {}
                    st["fails"] = 0
                else:
                    # Keep the batch and the offsets; retry on the next cycle.
                    st["fails"] += 1
                    logger.error(
                        f"{topic}: flush failed (attempt {st['fails']}), "
                        f"retaining {len(st['batch'])} records for retry"
                    )
                    if len(st["batch"]) > MAX_BATCH:
                        # Stop pulling more of a topic we cannot write; pausing keeps
                        # the data in Kafka instead of growing the heap without bound.
                        paused = [tp for tp in st["offsets"]]
                        if paused:
                            consumer.pause(*paused)
                            logger.error(f"{topic}: paused {len(paused)} partition(s) -- ClickHouse not accepting writes")
                    await asyncio.sleep(min(30.0, 2.0 * st["fails"]))
                st["last"] = time.monotonic()

            # Resume anything we paused once its backlog has drained.
            for topic, st in state.items():
                if st["fails"] == 0 and not st["batch"]:
                    resumable = [tp for tp in consumer.paused() if tp.topic == topic]
                    if resumable:
                        consumer.resume(*resumable)

    finally:
        # Flush what's left and commit it, otherwise a graceful restart replays
        # (and duplicates) the last uncommitted window.
        try:
            for topic, st in state.items():
                if st["batch"] and flushers[topic](st["batch"]) and st["offsets"]:
                    await consumer.commit({tp: off + 1 for tp, off in st["offsets"].items()})
        except Exception as e:
            logger.error(f"Failed to flush/commit on shutdown: {e}")
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
