from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
import json
import time
from datetime import datetime, UTC

from denoiser.storage.db import get_db, Span
from denoiser.api.auth import verify_ingest_auth

router = APIRouter(prefix="/v1", tags=["OTLP"])


@router.post("/logs")
async def ingest_otlp_logs(request: Request, tenant_id: str = Depends(verify_ingest_auth)):
    """
    Accepts OpenTelemetry standard JSON logs payload (resourceLogs) and ingestion.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    resource_logs = body.get("resourceLogs", [])
    extracted_logs = []

    for r_log in resource_logs:
        resource_attrs = {attr["key"]: attr["value"].get("stringValue", str(attr["value"])) 
                          for attr in r_log.get("resource", {}).get("attributes", [])}
        service_name = resource_attrs.get("service.name", "unknown_service")

        for scope_log in r_log.get("scopeLogs", []):
            for record in scope_log.get("logRecords", []):
                # OTLP timestamps can be nano-seconds string or milliseconds float
                time_nano = record.get("timeUnixNano")
                if time_nano:
                    ts = float(time_nano) / 1e9
                else:
                    ts = time.time()

                body_val = record.get("body", {})
                message = body_val.get("stringValue") or body_val.get("intValue") or json.dumps(body_val)

                log_entry = {
                    "timestamp": ts,
                    "service": service_name,
                    "level": record.get("severityText", "INFO").upper(),
                    "message": message,
                    "source": "otlp",
                    "attributes": {attr["key"]: attr["value"].get("stringValue", str(attr["value"])) 
                                   for attr in record.get("attributes", [])}
                }
                extracted_logs.append(log_entry)

    if not extracted_logs:
        return {"status": "success", "ingested": 0}

    # Write logs to standard live_stream.log file (fallback)
    from denoiser.api.main import DATA_DIR, clickhouse_store, redis_client
    stream_file = DATA_DIR / "live_stream.log"
    with open(stream_file, "a") as f:
        for log in extracted_logs:
            f.write(json.dumps(log) + "\n")

    # Insert to ClickHouse
    clickhouse_inserted = False
    if clickhouse_store.client:
        clickhouse_inserted = clickhouse_store.insert_logs(extracted_logs, tenant_id=tenant_id)

    # Publish to Redis
    try:
        for log in extracted_logs:
            await redis_client.publish("log_stream", json.dumps(log))
    except Exception:
        pass

    return {
        "status": "success",
        "ingested": len(extracted_logs),
        "clickhouse": clickhouse_inserted
    }


@router.post("/traces")
async def ingest_otlp_traces(request: Request, db: Session = Depends(get_db), tenant_id: str = Depends(verify_ingest_auth)):
    """
    Accepts OpenTelemetry standard JSON traces payload (resourceSpans) and ingestion.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    resource_spans = body.get("resourceSpans", [])
    db_spans = []
    clickhouse_rows = []

    for r_span in resource_spans:
        resource_attrs = {attr["key"]: attr["value"].get("stringValue", str(attr["value"])) 
                          for attr in r_span.get("resource", {}).get("attributes", [])}
        service_name = resource_attrs.get("service.name", "unknown_service")

        for scope_span in r_span.get("scopeSpans", []):
            for span_data in scope_span.get("spans", []):
                start_nano = span_data.get("startTimeUnixNano")
                end_nano = span_data.get("endTimeUnixNano")
                
                start_dt = datetime.fromtimestamp(float(start_nano) / 1e9, UTC) if start_nano else datetime.now(UTC)
                end_dt = datetime.fromtimestamp(float(end_nano) / 1e9, UTC) if end_nano else datetime.now(UTC)
                duration_ms = (float(end_nano) - float(start_nano)) / 1e6 if start_nano and end_nano else 0.0

                attributes = {attr["key"]: attr["value"].get("stringValue", str(attr["value"])) 
                              for attr in span_data.get("attributes", [])}
                
                status_code = span_data.get("status", {}).get("code", "STATUS_CODE_UNSET")

                # Database entity (for local testing/fallback SQLite)
                span = Span(
                    trace_id=span_data.get("traceId"),
                    span_id=span_data.get("spanId"),
                    parent_span_id=span_data.get("parentSpanId"),
                    service_name=service_name,
                    operation_name=span_data.get("name"),
                    start_time=start_dt,
                    end_time=end_dt,
                    duration_ms=duration_ms,
                    status_code=status_code,
                    attributes=attributes,
                    events=span_data.get("events", [])
                )
                db_spans.append(span)

                # ClickHouse tuple row mapping
                clickhouse_rows.append((
                    span_data.get("traceId"),
                    span_data.get("spanId"),
                    span_data.get("parentSpanId"),
                    service_name,
                    span_data.get("name"),
                    start_dt,
                    end_dt,
                    duration_ms,
                    status_code,
                    json.dumps(attributes),
                    json.dumps(span_data.get("events", []))
                ))

    # Save to local SQLite database
    for span in db_spans:
        db.add(span)
    db.commit()

    # Insert to ClickHouse
    from denoiser.api.main import clickhouse_store
    clickhouse_inserted = False
    if clickhouse_store.client and clickhouse_rows:
        clickhouse_inserted = clickhouse_store.insert_traces(clickhouse_rows, tenant_id=tenant_id)

    return {
        "status": "success",
        "spans_ingested": len(db_spans),
        "clickhouse": clickhouse_inserted
    }
