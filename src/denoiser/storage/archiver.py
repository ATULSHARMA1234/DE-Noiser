import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
from botocore.client import Config

from denoiser.logging import get_logger
from denoiser.storage.db import SessionLocal, Span

logger = get_logger(__name__)

ARCHIVE_DIR = Path("data") / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)


class S3ArchiverEngine:
    @staticmethod
    def get_s3_client(settings: dict):
        """Build configured boto3 S3 client."""
        return boto3.client(
            "s3",
            endpoint_url=settings.get("s3_endpoint", "http://localhost:9000"),
            aws_access_key_id=settings.get("s3_access_key", "admin"),
            aws_secret_access_key=settings.get("s3_secret_key", "password123"),
            config=Config(signature_version="s3v4"),
            region_name="us-east-1"
        )

    @staticmethod
    def run_archival():
        """
        Scan hot stores for old records, package them to gzip JSONL,
        upload to S3, and delete them from hot databases.
        """
        from denoiser.api.main import _load_settings, clickhouse_store
        settings = _load_settings()
        archive_days = settings.get("s3_archive_days", 7)
        cutoff_date = datetime.now(UTC) - timedelta(days=archive_days)

        logger.info(f"Running S3 Archival Engine with cutoff: {cutoff_date.isoformat()}")
        
        db = SessionLocal()
        try:
            # 1. Archive SQLite Traces (spans table)
            old_spans = db.query(Span).filter(Span.start_time < cutoff_date).all()
            if old_spans:
                span_data = []
                for s in old_spans:
                    span_data.append({
                        "trace_id": s.trace_id,
                        "span_id": s.span_id,
                        "parent_span_id": s.parent_span_id,
                        "service_name": s.service_name,
                        "operation_name": s.operation_name,
                        "start_time": s.start_time.isoformat() if s.start_time else None,
                        "end_time": s.end_time.isoformat() if s.end_time else None,
                        "duration_ms": s.duration_ms,
                        "status_code": s.status_code,
                        "attributes": s.attributes,
                        "events": s.events
                    })

                filename = f"traces_{int(datetime.now(UTC).timestamp())}.jsonl.gz"
                filepath = ARCHIVE_DIR / filename
                
                with gzip.open(filepath, "wt", encoding="utf-8") as f:
                    for item in span_data:
                        f.write(json.dumps(item) + "\n")
                
                # Upload to S3 if enabled
                if settings.get("s3_enabled"):
                    try:
                        s3 = S3ArchiverEngine.get_s3_client(settings)
                        bucket = settings.get("s3_bucket", "semanticos-logs")
                        s3.upload_file(str(filepath), bucket, f"archive/traces/{filename}")
                        logger.info(f"Uploaded traces archive {filename} to S3 bucket {bucket}")
                    except Exception as s3_err:
                        logger.error(f"Failed to upload traces archive to S3: {s3_err}")

                # Delete from SQLite
                for s in old_spans:
                    db.delete(s)
                db.commit()
                logger.info(f"Archived and pruned {len(old_spans)} spans from local SQLite database")

            # 2. Archive ClickHouse Logs (if ClickHouse is connected)
            if clickhouse_store.client:
                # Query old logs
                sql = f"SELECT * FROM semantic_logs WHERE timestamp < '{cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}'"
                try:
                    res = clickhouse_store.client.query(sql)
                    old_logs = [dict(zip(res.column_names, row, strict=False)) for row in res.result_rows]
                    
                    if old_logs:
                        filename = f"logs_{int(datetime.now(UTC).timestamp())}.jsonl.gz"
                        filepath = ARCHIVE_DIR / filename
                        
                        # Serialize datetimes to string
                        def default_converter(o):
                            if isinstance(o, datetime):
                                return o.isoformat()
                            return str(o)

                        with gzip.open(filepath, "wt", encoding="utf-8") as f:
                            for log in old_logs:
                                f.write(json.dumps(log, default=default_converter) + "\n")

                        # Upload to S3
                        if settings.get("s3_enabled"):
                            try:
                                s3 = S3ArchiverEngine.get_s3_client(settings)
                                bucket = settings.get("s3_bucket", "semanticos-logs")
                                s3.upload_file(str(filepath), bucket, f"archive/logs/{filename}")
                                logger.info(f"Uploaded logs archive {filename} to S3 bucket {bucket}")
                            except Exception as s3_err:
                                logger.error(f"Failed to upload logs archive to S3: {s3_err}")

                        # Prune from ClickHouse
                        clickhouse_store.client.command(f"""
                            ALTER TABLE semantic_logs
                            DELETE WHERE timestamp < '{cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}'
                        """)
                        logger.info(f"Archived and pruned {len(old_logs)} logs from ClickHouse table")
                except Exception as ch_err:
                    logger.error(f"ClickHouse archival failed: {ch_err}")

        except Exception as e:
            logger.error(f"Archival job failed: {e}")
            db.rollback()
        finally:
            db.close()

    @staticmethod
    def hydrate_archive(archive_filename: str) -> dict:
        """
        Hydrate archived logs/traces back into the hot database.
        """
        from denoiser.api.main import _load_settings, clickhouse_store
        settings = _load_settings()
        filepath = ARCHIVE_DIR / archive_filename

        # If not present locally, try downloading from S3 if enabled
        if not filepath.exists() and settings.get("s3_enabled"):
            try:
                s3 = S3ArchiverEngine.get_s3_client(settings)
                bucket = settings.get("s3_bucket", "semanticos-logs")
                folder = "traces" if "traces" in archive_filename else "logs"
                s3.download_file(bucket, f"archive/{folder}/{archive_filename}", str(filepath))
                logger.info(f"Downloaded archive {archive_filename} from S3 bucket {bucket}")
            except Exception as e:
                logger.error(f"Failed to download archive from S3: {e}")
                return {"status": "error", "message": f"Archive not found locally or on S3: {e}"}

        if not filepath.exists():
            return {"status": "error", "message": f"Archive file {archive_filename} not found"}

        restored_count = 0
        db = SessionLocal()
        try:
            with gzip.open(filepath, "rt", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    
                    if "traces" in archive_filename:
                        # Restore span
                        start_time = datetime.fromisoformat(item["start_time"]) if item.get("start_time") else None
                        end_time = datetime.fromisoformat(item["end_time"]) if item.get("end_time") else None
                        
                        # Avoid duplicates
                        exists = db.query(Span).filter(Span.span_id == item["span_id"]).first()
                        if not exists:
                            span = Span(
                                trace_id=item["trace_id"],
                                span_id=item["span_id"],
                                parent_span_id=item["parent_span_id"],
                                service_name=item["service_name"],
                                operation_name=item["operation_name"],
                                start_time=start_time,
                                end_time=end_time,
                                duration_ms=item["duration_ms"],
                                status_code=item["status_code"],
                                attributes=item["attributes"],
                                events=item["events"]
                            )
                            db.add(span)
                            restored_count += 1
                    else:
                        # Restore log in ClickHouse
                        if clickhouse_store.client:
                            # Parse dates
                            ts_str = item.get("timestamp")
                            dt = datetime.fromisoformat(ts_str) if ts_str else datetime.now(UTC)
                            
                            clickhouse_store.client.insert('semantic_logs', [(
                                item.get("tenant_id", "default"),
                                dt,
                                item.get("source", "unknown"),
                                item.get("level", "INFO"),
                                item.get("message", ""),
                                item.get("raw_json", "{}")
                            )], column_names=['tenant_id', 'timestamp', 'source', 'level', 'message', 'raw_json'])
                            restored_count += 1

            db.commit()
            return {"status": "success", "restored": restored_count}
        except Exception as e:
            logger.error(f"Failed to hydrate archive: {e}")
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()
