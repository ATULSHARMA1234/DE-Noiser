import gzip
import os
import shutil
import time
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from denoiser.api.joblock import single_instance
from denoiser.logging import get_logger
from denoiser.storage.object_store import ObjectStore

logger = get_logger(__name__)

DATA_DIR = Path("data")

def get_storage_settings():
    """Settings as every replica sees them (database-backed, not a local file)."""
    try:
        from denoiser.api.platform_settings import load_settings

        return load_settings()
    except Exception as e:
        logger.error(f"Could not load settings for the retention job: {e}")
        return {}

def get_retention_days():
    return get_storage_settings().get("retention_days", 7)

async def archive_old_logs_to_s3():
    """
    Finds log files older than `retention_days`, compresses them, uploads to S3,
    and deletes the local files. This prevents disk space exhaustion.
    """
    logger.info("Running S3 Retention Scheduler Job...")
    storage_settings = get_storage_settings()
    if not storage_settings.get("s3_enabled", False):
        logger.info("S3 archival is disabled in settings; skipping retention upload.")
        return

    retention_days = int(storage_settings.get("retention_days", get_retention_days()))
    cutoff_time = time.time() - (retention_days * 86400)

    try:
        store = ObjectStore(
            endpoint_url=storage_settings.get("s3_endpoint"),
            access_key=storage_settings.get("s3_access_key"),
            secret_key=storage_settings.get("s3_secret_key"),
            bucket_name=storage_settings.get("s3_bucket"),
        )
    except Exception as e:
        logger.error(f"Failed to initialize S3 ObjectStore. Archiving aborted: {e}")
        return

    # Check all log files except the live stream and the settings/db files
    for ext in ["*.log", "*.jsonl"]:
        for file_path in DATA_DIR.glob(ext):
            if file_path.name == "live_stream.log":
                continue  # Let log rotation handle the live file

            stat = file_path.stat()
            if stat.st_mtime < cutoff_time:
                # 1. Compress it
                gz_path = str(file_path) + ".gz"
                try:
                    with open(file_path, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)

                    # 2. Upload to S3
                    object_name = f"archive/{file_path.name}.gz"
                    if store.upload_file(gz_path, object_name):
                        logger.info(f"Successfully archived {file_path.name} to S3.")
                        # 3. Clean up local files
                        os.remove(file_path)
                        os.remove(gz_path)
                    else:
                        logger.error(f"Upload failed for {gz_path}. Will retry next cron run.")
                        os.remove(gz_path)
                except Exception as e:
                    logger.error(f"Error archiving file {file_path.name}: {e}")

async def cleanup_database_records():
    """
    Deletes AnalysisRun and AuditLog records older than their tenant's retention_days.

    Per tenant: this used one deployment-wide value, so one organisation's
    ADMIN choosing 7 days deleted every other organisation's history at 7 days
    too. Rows with no tenant use the deployment value.
    """
    logger.info("Running Database Retention Scheduler Job...")
    import datetime

    from denoiser.api.platform_settings import load_tenant_settings
    from denoiser.storage.db import AnalysisRun, AuditLog, SessionLocal, Tenant
    from denoiser.utils.time import utcnow

    def cutoff(tenant_id):
        days = int(load_tenant_settings(tenant_id, db).get("retention_days") or get_retention_days())
        return utcnow() - datetime.timedelta(days=days)

    db = SessionLocal()
    try:
        deleted_runs = deleted_logs = 0
        for (tenant_id,) in [*db.query(Tenant.id).all(), (None,)]:
            before = cutoff(tenant_id)
            runs = db.query(AnalysisRun).filter(AnalysisRun.created_at < before)
            logs = db.query(AuditLog).filter(AuditLog.timestamp < before)
            if tenant_id is None:
                runs = runs.filter(AnalysisRun.tenant_id.is_(None))
                logs = logs.filter(AuditLog.tenant_id.is_(None))
            else:
                runs = runs.filter(AnalysisRun.tenant_id == tenant_id)
                logs = logs.filter(AuditLog.tenant_id == tenant_id)
            deleted_runs += runs.delete(synchronize_session=False)
            deleted_logs += logs.delete(synchronize_session=False)

        db.commit()
        logger.info(f"Deleted {deleted_runs} old analysis runs and {deleted_logs} old audit logs.")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to cleanup database records: {e}")
    finally:
        db.close()

async def trigger_sso_s3_db_archival():
    """Run S3ArchiverEngine.run_archival().

    No longer on a schedule of its own — see the note by the job registrations
    below. Kept as a callable so an operator can still invoke the sweep out of
    band (the `/storage` endpoint does exactly that).
    """
    from denoiser.storage.archiver import S3ArchiverEngine
    try:
        S3ArchiverEngine.run_archival()
    except Exception as e:
        logger.error(f"S3ArchiverEngine background job failed: {e}")

scheduler = AsyncIOScheduler()

#: The nightly schedule, as data: ``(job id, callable, hour, minute)``.
#:
#: Declared rather than registered inline so that what is scheduled can be read
#: — and asserted — without a live scheduler. Job registration is process state,
#: and process state is not where a policy decision should only be visible.
#:
#: Every job is wrapped so that only one replica executes a given occurrence.
#: APScheduler is in-process: without this, N replicas each run the nightly
#: sweep, racing to gzip, upload and delete the same files.
#:
#: `archive_old_logs_to_s3` sweeps *log files on local disk*. It is unrelated to
#: the ClickHouse and span archival — it protects the data volume, not the
#: retention policy — and the similar name is why the two were once confused.
#:
#: `trigger_sso_s3_db_archival` — the store archival — is deliberately absent.
#: It ran at 04:00, while retention (a hard DELETE against the same rows, on the
#: same seven-day threshold) ran at 00:00 on the Celery beat, in a different
#: process. The destructive job won by four hours, so a free-tier tenant's logs
#: were deleted from ClickHouse before the job that would have written them to
#: S3 came looking. Both reported success and the archive stayed empty.
#:
#: It now runs inside `denoiser.workers.billing_worker.aggregate_billing`,
#: immediately before the deletion it protects, and the deletion is skipped when
#: it fails. Ordering that matters cannot live in two crontabs in two processes.
NIGHTLY_JOBS: tuple[tuple[str, object, int, int], ...] = (
    ("archive_old_logs_to_s3", archive_old_logs_to_s3, 2, 0),
    ("cleanup_database_records", cleanup_database_records, 3, 0),
)

for _job_id, _func, _hour, _minute in NIGHTLY_JOBS:
    scheduler.add_job(
        single_instance(_job_id)(_func), "cron", hour=_hour, minute=_minute, id=_job_id
    )


def scheduler_enabled() -> bool:
    """Set SEMANTICOS_SCHEDULER_ENABLED=0 on replicas that should not schedule.

    The lock already makes it safe to leave on everywhere. Turning it off lets
    you run retention on a dedicated instance instead, which is easier to
    reason about and to alert on.
    """
    return os.getenv("SEMANTICOS_SCHEDULER_ENABLED", "1").lower() in ("1", "true", "yes")


def start_scheduler():
    if not scheduler_enabled():
        logger.info("Scheduler disabled via SEMANTICOS_SCHEDULER_ENABLED")
        return
    if scheduler.running:
        return
    scheduler.start()
    logger.info("Data retention APScheduler started.")

def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
