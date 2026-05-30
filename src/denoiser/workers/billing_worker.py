import datetime
import os
from celery import Celery
from denoiser.logging import get_logger
from denoiser.storage.db import SessionLocal, BillingMeter, Tenant
from denoiser.storage.clickhouse_store import ClickHouseStore

logger = get_logger(__name__)

celery_app = Celery("billing_worker", broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"))

@celery_app.task(name="aggregate_daily_billing")
def aggregate_daily_billing():
    """
    Cron job to calculate usage meters per tenant for the current day.
    """
    logger.info("Starting daily billing aggregation...")
    db = SessionLocal()
    ch_store = ClickHouseStore()
    try:
        tenants = db.query(Tenant).all()
        today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        for tenant in tenants:
            try:
                # Query ClickHouse for total logs today
                tenant_id = str(tenant.id)
                query = f"""
                    SELECT count(), sum(length(message)) 
                    FROM semantic_logs 
                    WHERE tenant_id = '{tenant_id}' 
                      AND toDate(timestamp) = toDate(now())
                """
                result = ch_store.client.query(query).result_rows
                count = result[0][0] if result else 0
                bytes_ingested = result[0][1] if result and result[0][1] else 0
                
                meter = db.query(BillingMeter).filter(
                    BillingMeter.tenant_id == tenant.id,
                    BillingMeter.date == today
                ).first()
                
                if not meter:
                    meter = BillingMeter(
                        tenant_id=tenant.id,
                        date=today,
                        total_logs_ingested=count,
                        total_bytes_ingested=bytes_ingested,
                        total_traces_ingested=0
                    )
                    db.add(meter)
                else:
                    meter.total_logs_ingested = count
                    meter.total_bytes_ingested = bytes_ingested
                    
                # Implement Data Tiering (TTL policies based on tier)
                days_to_keep = 7
                if tenant.tier == "pro":
                    days_to_keep = 30
                elif tenant.tier == "enterprise":
                    days_to_keep = 90
                
                ch_store.cleanup_old_data(tenant_id, days_to_keep)
                    
            except Exception as e:
                logger.error(f"Failed to calculate billing for tenant {tenant.id}: {e}")
                
        db.commit()
        logger.info("Daily billing aggregation complete.")
    finally:
        db.close()

@celery_app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # Execute every day at midnight (or every hour for testing)
    from celery.schedules import crontab
    sender.add_periodic_task(
        crontab(minute=0, hour=0),
        aggregate_daily_billing.s(),
        name='aggregate_billing_daily'
    )
