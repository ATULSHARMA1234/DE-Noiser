import asyncio
import json
import os
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
        # Simple batching mechanism
        batch_logs = []
        batch_traces = []
        BATCH_SIZE = 1000
        
        async for msg in consumer:
            topic = msg.topic
            try:
                payload = json.loads(msg.value.decode('utf-8'))
                tenant_id = payload.pop("_tenant_id", "default_tenant")
                
                if topic == "logs_topic":
                    # Attach tenant_id back if it's missing or handle it
                    batch_logs.append((payload, tenant_id))
                    
                    if len(batch_logs) >= BATCH_SIZE:
                        # Flush logs
                        _flush_logs(ch_store, batch_logs)
                        batch_logs = []
                
                elif topic == "traces_topic":
                    batch_traces.append((payload, tenant_id))
                    
                    if len(batch_traces) >= BATCH_SIZE:
                        # Flush traces
                        _flush_traces(batch_traces)
                        batch_traces = []
                        
            except Exception as e:
                logger.error(f"Failed to process message from {topic}: {e}")

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
