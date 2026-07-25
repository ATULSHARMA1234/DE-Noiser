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
SETTINGS_FILE = DATA_DIR / "settings.json"

def get_retention_days():
    import json
    try:
        if SETTINGS_FILE.exists():
            cfg = json.loads(SETTINGS_FILE.read_text())
            return cfg.get("retention_days", 7)
    except Exception:
        pass
    return 7

def get_storage_settings():
    import json
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        pass
    return {}

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
    Deletes AnalysisRun and AuditLog records older than the configured retention_days.
    """
    logger.info("Running Database Retention Scheduler Job...")
    retention_days = int(get_retention_days())
    import datetime

    from denoiser.utils.time import utcnow
    cutoff_time = utcnow() - datetime.timedelta(days=retention_days)

    try:
        from denoiser.storage.db import AnalysisRun, AuditLog, SessionLocal
        db = SessionLocal()

        # Delete old AnalysisRuns
        deleted_runs = db.query(AnalysisRun).filter(AnalysisRun.created_at < cutoff_time).delete()

        # Delete old AuditLogs
        deleted_logs = db.query(AuditLog).filter(AuditLog.timestamp < cutoff_time).delete()

        db.commit()
        logger.info(f"Deleted {deleted_runs} old analysis runs and {deleted_logs} old audit logs.")
    except Exception as e:
        logger.error(f"Failed to cleanup database records: {e}")
    finally:
        db.close()

async def trigger_sso_s3_db_archival():
    """Daily job to run S3ArchiverEngine.run_archival()"""
    from denoiser.storage.archiver import S3ArchiverEngine
    try:
        S3ArchiverEngine.run_archival()
    except Exception as e:
        logger.error(f"S3ArchiverEngine background job failed: {e}")

scheduler = AsyncIOScheduler()

# Every job is wrapped so that only one replica executes a given occurrence.
# APScheduler is in-process: without this, N replicas each run the nightly
# archival, racing to gzip, upload and delete the same files.
scheduler.add_job(single_instance("archive_old_logs_to_s3")(archive_old_logs_to_s3), 'cron', hour=2, minute=0)
scheduler.add_job(single_instance("cleanup_database_records")(cleanup_database_records), 'cron', hour=3, minute=0)
scheduler.add_job(single_instance("trigger_sso_s3_db_archival")(trigger_sso_s3_db_archival), 'cron', hour=4, minute=0)


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
