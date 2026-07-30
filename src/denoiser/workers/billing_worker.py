"""Per-tenant usage metering and tier-based data retention.

This module had a Celery app and a midnight crontab of its own, so running it
meant starting a *second* beat process that nothing in the deploy — no Helm
template, no compose service, no documented launch step — ever started. The
aggregation was correct and never once executed: `BillingMeter` stayed empty
and retention was never enforced.

The work now lives in a plain function, and the platform's single beat schedule
(``denoiser.workers.analysis_worker``) drives it. The Celery app below is kept
so an operator who wants metering on a dedicated worker still can, but nothing
depends on that any more.
"""

import os

from celery import Celery
from sqlalchemy import func

from denoiser import runtime
from denoiser.logging import get_logger
from denoiser.storage.db import BillingMeter, SessionLocal, Span, Tenant
from denoiser.utils.time import utcnow

logger = get_logger(__name__)

celery_app = Celery("billing_worker", broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"))

# Retention granted by tier, in days. Enforced by the same pass that meters
# usage, so a tenant's storage footprint tracks what they pay for.
RETENTION_DAYS_BY_TIER = {
    "free": 7,
    "pro": 30,
    "enterprise": 90,
}
DEFAULT_RETENTION_DAYS = 7


def aggregate_billing(db=None, *, enforce_retention: bool = True) -> dict:
    """Meter today's usage for every tenant and apply their retention policy.

    Returns a summary so the caller (and the task log) can tell the difference
    between "ran and metered 4 tenants" and "ran and silently did nothing" —
    which is exactly the distinction that was missing while this never ran.
    """
    logger.info("Starting daily billing aggregation...")
    owns_session = db is None
    db = db or SessionLocal()
    ch_store = runtime.clickhouse_store()

    summary = {"tenants": 0, "metered": 0, "failed": 0, "logs": 0, "bytes": 0, "traces": 0}

    try:
        tenants = db.query(Tenant).all()
        summary["tenants"] = len(tenants)
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        for tenant in tenants:
            try:
                count = 0
                bytes_ingested = 0

                if not ch_store.available:
                    # Metering a customer at zero because we could not read the
                    # store commits a day of "they sent us nothing" that is
                    # indistinguishable from the truth once written. Skip the
                    # tenant entirely and count it as failed, so the summary
                    # says so and the pass can be re-run.
                    summary["failed"] += 1
                    logger.warning(
                        "ClickHouse unavailable — tenant %s not metered this run", tenant.id
                    )
                    continue

                if ch_store.client is not None:
                    # The window comes from the store rather than being typed
                    # out here: it is the only module that knows how
                    # `semantic_logs` is partitioned, and it will not build a
                    # clause without a tenant.
                    where, params = ch_store.scope(tenant.id)
                    result = ch_store.client.query(
                        "SELECT count(), sum(length(message)) FROM semantic_logs "
                        f"WHERE {where} AND toDate(timestamp) = toDate(now())",
                        parameters=params,
                    ).result_rows
                    count = result[0][0] if result else 0
                    bytes_ingested = result[0][1] if result and result[0][1] else 0

                # Traces live in Postgres, not ClickHouse. This was hardcoded to
                # 0, so trace-heavy tenants metered as if they sent none.
                traces = (
                    db.query(func.count(func.distinct(Span.trace_id)))
                    .filter(Span.tenant_id == tenant.id, Span.start_time >= today)
                    .scalar()
                    or 0
                )

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
                        total_traces_ingested=traces,
                    )
                    db.add(meter)
                else:
                    meter.total_logs_ingested = count
                    meter.total_bytes_ingested = bytes_ingested
                    meter.total_traces_ingested = traces

                if enforce_retention:
                    days_to_keep = RETENTION_DAYS_BY_TIER.get(
                        (tenant.tier or "").lower(), DEFAULT_RETENTION_DAYS
                    )
                    ch_store.cleanup_old_data(str(tenant.id), days_to_keep)

                summary["metered"] += 1
                summary["logs"] += count
                summary["bytes"] += bytes_ingested
                summary["traces"] += traces

            except Exception as e:
                summary["failed"] += 1
                logger.error(f"Failed to calculate billing for tenant {tenant.id}: {e}")

        db.commit()
        logger.info(
            "Daily billing aggregation complete: metered %d/%d tenants (%d logs, %d traces)",
            summary["metered"], summary["tenants"], summary["logs"], summary["traces"],
        )
        return summary
    except Exception as e:
        logger.error(f"Billing aggregation failed: {e}")
        db.rollback()
        summary["error"] = str(e)
        return summary
    finally:
        if owns_session:
            db.close()


@celery_app.task(name="aggregate_daily_billing")
def aggregate_daily_billing():
    """Standalone entry point, for running metering on a dedicated worker."""
    return aggregate_billing()


@celery_app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    """Schedule for the optional dedicated billing beat.

    The platform's own beat already schedules this work (see
    ``analysis_worker.setup_periodic_tasks``); do not run both.
    """
    from celery.schedules import crontab
    sender.add_periodic_task(
        crontab(minute=0, hour=0),
        aggregate_daily_billing.s(),
        name='aggregate_billing_daily'
    )
