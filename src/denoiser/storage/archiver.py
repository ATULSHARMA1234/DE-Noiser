import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
from botocore.client import Config

from denoiser import runtime
from denoiser.logging import get_logger
from denoiser.storage.db import SessionLocal, Span

logger = get_logger(__name__)

ARCHIVE_DIR = Path("data") / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)

#: Bucket for rows whose tenant was never recorded. They are kept separate from
#: every real customer rather than folded into one, so a restore cannot deposit
#: unattributed data into somebody's account.
UNKNOWN_TENANT = "unknown"


def _tenant_key(value) -> str:
    text = "" if value is None else str(value).strip()
    # Restricted to characters that are safe in both a filename and an S3 key,
    # since the tenant id becomes part of both.
    if not text or not all(c.isalnum() or c in "-_" for c in text):
        return UNKNOWN_TENANT
    return text


def archive_object_key(filename: str) -> str:
    """Where an archive file lives in the bucket, derived from its name.

    Archives are partitioned by tenant — one gzip per customer per run rather
    than one containing everybody's rows — so that a customer's cold data can be
    located and deleted when they leave, which a commingled file makes
    impossible.
    """
    kind = "traces" if filename.startswith("traces_") else "logs"
    parts = filename.split("_")
    tenant = parts[1][1:] if len(parts) > 2 and parts[1].startswith("t") else UNKNOWN_TENANT
    return f"archive/{kind}/tenant={tenant or UNKNOWN_TENANT}/{filename}"


def _write_archive(prefix: str, tenant: str, rows: list[dict]) -> tuple[Path, str]:
    """Write one tenant's rows to a gzip JSONL file. Returns (path, filename)."""
    def default_converter(o):
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)

    filename = f"{prefix}_t{tenant}_{int(datetime.now(UTC).timestamp())}.jsonl.gz"
    filepath = ARCHIVE_DIR / filename
    with gzip.open(filepath, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=default_converter) + "\n")
    return filepath, filename


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
        # Settings and the store come from denoiser.runtime and
        # denoiser.api.platform_settings. This module used to import the whole
        # HTTP application to reach them, which is how a storage module ended up
        # depending on the API.
        from denoiser.api.platform_settings import load_settings

        settings = load_settings()
        clickhouse_store = runtime.clickhouse_store()
        archive_days = settings.get("s3_archive_days", 7)
        cutoff_date = datetime.now(UTC) - timedelta(days=archive_days)

        logger.info(f"Running S3 Archival Engine with cutoff: {cutoff_date.isoformat()}")
        
        db = SessionLocal()
        try:
            # 1. Archive SQLite Traces (spans table)
            # Streamed, not materialised. `.all()` loaded every expired span
            # into memory and the grouping below then built a second full copy,
            # so peak use was roughly twice the row set — inside the API
            # process, because this ran under APScheduler rather than in a
            # worker. A week of missed runs was enough to OOM the pod that
            # serves requests.
            #
            # A hard ceiling as well as batching: a sweep that has not run in a
            # month should take a month's worth per pass and leave the rest for
            # the next one, rather than trying to do all of it and failing.
            max_per_pass = int(settings.get("archive_max_spans_per_pass", 200_000))
            old_spans = (
                db.query(Span)
                .filter(Span.start_time < cutoff_date)
                .order_by(Span.id)
                .limit(max_per_pass)
                .yield_per(5_000)
            )
            old_spans = list(old_spans)
            if len(old_spans) == max_per_pass:
                logger.warning(
                    "Archival hit its %d-span ceiling; the remainder will be taken "
                    "by the next pass", max_per_pass,
                )
            if old_spans:
                # Grouped by owner so each customer's cold traces are their own
                # object, which is what makes them deletable on offboarding.
                by_tenant: dict[str, list[dict]] = {}
                for s in old_spans:
                    by_tenant.setdefault(_tenant_key(s.tenant_id), []).append({
                        "tenant_id": s.tenant_id,
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

                for tenant, span_data in by_tenant.items():
                    filepath, filename = _write_archive("traces", tenant, span_data)

                    # Upload to S3 if enabled
                    if settings.get("s3_enabled"):
                        try:
                            s3 = S3ArchiverEngine.get_s3_client(settings)
                            bucket = settings.get("s3_bucket", "semanticos-logs")
                            s3.upload_file(str(filepath), bucket, archive_object_key(filename))
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
                # Archival is deliberately deployment-wide — it sweeps every
                # organisation's cold rows — but the cutoff is operator-supplied
                # and reaches a DELETE, so it is bound as a parameter rather
                # than formatted into the statement.
                cutoff_params = {"cutoff": cutoff_date.timestamp()}
                sql = (
                    "SELECT * FROM semantic_logs "
                    "WHERE timestamp < toDateTime64({cutoff:Float64}, 3, 'UTC')"
                )
                try:
                    res = clickhouse_store.client.query(sql, parameters=cutoff_params)
                    old_logs = [dict(zip(res.column_names, row, strict=False)) for row in res.result_rows]

                    if old_logs:
                        logs_by_tenant: dict[str, list[dict]] = {}
                        for log in old_logs:
                            logs_by_tenant.setdefault(_tenant_key(log.get("tenant_id")), []).append(log)

                        for tenant, tenant_logs in logs_by_tenant.items():
                            filepath, filename = _write_archive("logs", tenant, tenant_logs)

                            # Upload to S3
                            if settings.get("s3_enabled"):
                                try:
                                    s3 = S3ArchiverEngine.get_s3_client(settings)
                                    bucket = settings.get("s3_bucket", "semanticos-logs")
                                    s3.upload_file(str(filepath), bucket, archive_object_key(filename))
                                    logger.info(f"Uploaded logs archive {filename} to S3 bucket {bucket}")
                                except Exception as s3_err:
                                    logger.error(f"Failed to upload logs archive to S3: {s3_err}")

                        # Prune only as far as the rows we actually wrote out.
                        # Deleting by the cutoff again would drop anything that
                        # landed between the SELECT and the DELETE — including
                        # backfilled rows, whose timestamps are old by
                        # definition — without archiving it first.
                        newest_archived = max(
                            (log["timestamp"] for log in old_logs if log.get("timestamp")),
                            default=None,
                        )
                        if newest_archived is not None:
                            clickhouse_store.client.command(
                                "ALTER TABLE semantic_logs DELETE "
                                "WHERE timestamp <= toDateTime64({newest:Float64}, 3, 'UTC')",
                                parameters={"newest": newest_archived.timestamp()},
                            )
                            logger.info(
                                f"Archived and pruned {len(old_logs)} logs from ClickHouse table"
                            )
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
        # Settings and the store come from denoiser.runtime and
        # denoiser.api.platform_settings. This module used to import the whole
        # HTTP application to reach them, which is how a storage module ended up
        # depending on the API.
        from denoiser.api.platform_settings import load_settings

        settings = load_settings()
        clickhouse_store = runtime.clickhouse_store()
        filepath = ARCHIVE_DIR / archive_filename

        # If not present locally, try downloading from S3 if enabled
        if not filepath.exists() and settings.get("s3_enabled"):
            try:
                s3 = S3ArchiverEngine.get_s3_client(settings)
                bucket = settings.get("s3_bucket", "semanticos-logs")
                s3.download_file(bucket, archive_object_key(archive_filename), str(filepath))
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
                                # Restored without this, every rehydrated span
                                # came back unattributed — archived under one
                                # customer and returned belonging to nobody.
                                tenant_id=item.get("tenant_id"),
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
                            
                            # Through `insert_logs` rather than a raw insert, so
                            # the restore runs the same tenant guard, source and
                            # level resolution the live ingest path does. A row
                            # with no recorded owner goes to the unattributed
                            # bucket, not to whichever real organisation happens
                            # to be called "default" — which is what the raw
                            # insert used to hand it to.
                            clickhouse_store.insert_logs(
                                [{
                                    "timestamp": dt,
                                    "source": item.get("source", "unknown"),
                                    "level": item.get("level", "INFO"),
                                    "message": item.get("message", ""),
                                }],
                                tenant_id=_tenant_key(item.get("tenant_id")),
                                # Already redacted on the way in — this row was
                                # archived from the store, not received from a
                                # customer. Re-running the patterns over every
                                # restored record is work with no effect.
                                redact=False,
                            )
                            restored_count += 1

            db.commit()
            return {"status": "success", "restored": restored_count}
        except Exception as e:
            logger.error(f"Failed to hydrate archive: {e}")
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()
