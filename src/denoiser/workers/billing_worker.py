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
from datetime import UTC, date, datetime, time, timedelta

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


def _upsert_meter(db, *, tenant_id: int, day_start, logs: int, bytes_ingested: int, traces: int) -> None:
    """Write one tenant-day meter, replacing whatever was there.

    Backed by the unique constraint on ``(tenant_id, date)``, so two passes
    racing over the same day converge on one row instead of producing two.
    Re-metering is an update: a day counted again is a correction, not an
    addition.
    """
    values = {
        "tenant_id": tenant_id,
        "date": day_start,
        "total_logs_ingested": logs,
        "total_bytes_ingested": bytes_ingested,
        "total_traces_ingested": traces,
    }
    dialect = db.get_bind().dialect.name

    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
    else:
        # No dialect-native upsert available; fall back to the read-then-write,
        # which the constraint still protects from producing a duplicate row.
        meter = db.query(BillingMeter).filter(
            BillingMeter.tenant_id == tenant_id, BillingMeter.date == day_start
        ).first()
        if meter is None:
            db.add(BillingMeter(**values))
        else:
            meter.total_logs_ingested = logs
            meter.total_bytes_ingested = bytes_ingested
            meter.total_traces_ingested = traces
        return

    statement = _insert(BillingMeter.__table__).values(**values)
    db.execute(statement.on_conflict_do_update(
        index_elements=["tenant_id", "date"],
        set_={
            "total_logs_ingested": statement.excluded.total_logs_ingested,
            "total_bytes_ingested": statement.excluded.total_bytes_ingested,
            "total_traces_ingested": statement.excluded.total_traces_ingested,
        },
    ))


def _archive_before_deleting() -> bool:
    """Run the S3 archival sweep, and report whether it completed.

    Retention must not proceed on a failed archive: deleting data whose only
    remaining copy was never written is not a retention policy, it is data loss
    with a schedule. The caller checks the return value.
    """
    from denoiser.storage.archiver import S3ArchiverEngine

    try:
        S3ArchiverEngine.run_archival()
        return True
    except Exception as e:
        logger.error("Archival failed; retention will be skipped this pass: %s", e)
        return False


def aggregate_billing(db=None, *, day: date | None = None, enforce_retention: bool = True) -> dict:
    """Meter one full day of usage for every tenant and apply their retention policy.

    ``day`` defaults to *yesterday*, and that default is the entire point. This
    ran on a midnight crontab and asked ClickHouse for
    ``toDate(timestamp) = toDate(now())`` — at 00:00:00 UTC, ``now()`` is the new
    day, so the window held the handful of seconds since midnight and the day
    that had just ended was never read at all. Every meter written was
    approximately zero, on a schedule, with no error to notice.

    Taking the day as an argument also makes a missed run recoverable: metering
    that can only ever mean "now" cannot be backfilled.

    Returns a summary so the caller (and the task log) can tell the difference
    between "ran and metered 4 tenants" and "ran and silently did nothing" —
    which is exactly the distinction that was missing while this never ran.
    """
    day = day or (utcnow().date() - timedelta(days=1))
    logger.info("Starting billing aggregation for %s...", day.isoformat())
    owns_session = db is None
    db = db or SessionLocal()
    ch_store = runtime.clickhouse_store()

    summary = {
        "day": day.isoformat(),
        "tenants": 0, "metered": 0, "failed": 0, "logs": 0, "bytes": 0, "traces": 0,
        "archived": False,
    }

    try:
        if enforce_retention:
            # Archival first, in the same pass, because retention below issues a
            # hard DELETE and the archive is the only copy that survives it.
            #
            # These used to be two jobs on two schedulers in two processes:
            # retention on the Celery beat at 00:00, archival on the API's
            # APScheduler at 02:00 — with the same seven-day threshold. The
            # destructive one ran first, so a free-tier tenant's logs were
            # deleted from ClickHouse two hours before the job that would have
            # written them to S3 went looking for them. Both jobs reported
            # success. Nothing was archived and nothing said so.
            #
            # Ordering that matters cannot live in two crontabs that happen to
            # be two hours apart.
            summary["archived"] = _archive_before_deleting()

        tenants = db.query(Tenant).all()
        summary["tenants"] = len(tenants)
        # The stored `date` stays a midnight datetime, so existing rows and
        # readers are unaffected; only which day it refers to has changed.
        day_start = datetime.combine(day, time.min, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)

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
                    #
                    # The day is bound, not derived from `now()`. Deriving it is
                    # what made this meter an empty window on a midnight
                    # schedule, and it is also what made a backfill impossible.
                    where, params = ch_store.scope(tenant.id)
                    params = {**params, "day": day.isoformat()}
                    result = ch_store.client.query(
                        "SELECT count(), sum(length(message)) FROM semantic_logs "
                        f"WHERE {where} AND toDate(timestamp) = toDate({{day:String}})",
                        parameters=params,
                    ).result_rows
                    count = result[0][0] if result else 0
                    bytes_ingested = result[0][1] if result and result[0][1] else 0

                # Traces live in Postgres, not ClickHouse. This was hardcoded to
                # 0, so trace-heavy tenants metered as if they sent none.
                # Half-open bounds, so a span at the stroke of midnight is
                # counted once rather than on both days.
                traces = (
                    db.query(func.count(func.distinct(Span.trace_id)))
                    .filter(
                        Span.tenant_id == tenant.id,
                        Span.start_time >= day_start,
                        Span.start_time < day_end,
                    )
                    .scalar()
                    or 0
                )

                # An upsert, not read-then-write. The old shape was
                # check-then-act with nothing behind it: two beats — the module
                # docstring notes a second one is possible — or a manual re-run
                # overlapping the scheduled one, and both saw "no row yet" and
                # both inserted. Summing a duplicated day overbills the
                # customer, which is the worse direction to be wrong in.
                _upsert_meter(
                    db,
                    tenant_id=tenant.id,
                    day_start=day_start,
                    logs=count,
                    bytes_ingested=bytes_ingested,
                    traces=traces,
                )

                # Only ever after a successful archive. A failed sweep leaves
                # the cold rows where they are — a day of extra storage is
                # recoverable, a deletion is not.
                if enforce_retention and summary["archived"]:
                    days_to_keep = RETENTION_DAYS_BY_TIER.get(
                        (tenant.tier or "").lower(), DEFAULT_RETENTION_DAYS
                    )
                    ch_store.cleanup_old_data(str(tenant.id), days_to_keep)

                # Per tenant, not once at the end. A single tenant whose write
                # fails — an out-of-range counter, a constraint, a lost
                # connection — used to reach the outer handler and roll back
                # every other tenant's meter along with its own.
                db.commit()

                summary["metered"] += 1
                summary["logs"] += count
                summary["bytes"] += bytes_ingested
                summary["traces"] += traces

            except Exception as e:
                db.rollback()
                summary["failed"] += 1
                logger.error(f"Failed to calculate billing for tenant {tenant.id}: {e}")

        # Reported after the meters are written, and from the meters themselves,
        # so what the customer is charged is exactly what the platform recorded.
        # A second, independently computed number is a number that can disagree
        # with the one on their usage page.
        try:
            from denoiser.api.billing import report_usage_for_day

            summary["usage_reported"] = report_usage_for_day(db, day)
        except Exception as e:
            # Never fatal: metering is the record, reporting is a delivery, and
            # a failed delivery is re-runnable while a lost meter is not.
            logger.error("Usage reporting failed for %s: %s", day.isoformat(), e)
            summary["usage_reported"] = {"error": str(e)}

        logger.info(
            "Billing aggregation for %s complete: metered %d/%d tenants (%d logs, %d traces)",
            day.isoformat(), summary["metered"], summary["tenants"],
            summary["logs"], summary["traces"],
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
def aggregate_daily_billing(day: str | None = None):
    """Standalone entry point, for running metering on a dedicated worker.

    ``day`` is an ISO date; omitted, it meters yesterday.
    """
    return aggregate_billing(day=date.fromisoformat(day) if day else None)


def backfill(start: date, end: date, *, enforce_retention: bool = False) -> list[dict]:
    """Re-run metering for every day in ``[start, end]`` inclusive.

    Metering that can only mean "now" cannot be repaired, and this pass has
    already run for a long time against an empty window. Retention is off by
    default: re-metering an old day should not also re-apply a deletion policy
    to data that has since changed tier.
    """
    if end < start:
        raise ValueError(f"end ({end}) is before start ({start})")

    results = []
    current = start
    while current <= end:
        results.append(aggregate_billing(day=current, enforce_retention=enforce_retention))
        current += timedelta(days=1)
    return results


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
