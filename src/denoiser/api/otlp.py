import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from denoiser import runtime
from denoiser.api.auth import verify_ingest_auth
from denoiser.storage.db import Span, get_db

router = APIRouter(prefix="/v1", tags=["OTLP"])


@router.post("/logs")
async def ingest_otlp_logs(request: Request, tenant_id: str = Depends(verify_ingest_auth)):
    """
    OTLP logs ingestion. Accepts the OTLP/HTTP **protobuf** encoding (the OTel
    default) as well as JSON, decoding both to the standard log record shape and
    writing them through the same path as every other source.
    """
    raw = await request.body()
    content_type = request.headers.get("content-type", "").lower()

    from denoiser.api.otlp_logs import decode_logs_json, decode_logs_request

    try:
        if "protobuf" in content_type or (raw[:1] not in (b"{", b"[") if raw else False):
            extracted_logs = decode_logs_request(raw)
        else:
            extracted_logs = decode_logs_json(json.loads(raw))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid OTLP logs payload: {e}")

    if not extracted_logs:
        return {"status": "success", "ingested": 0}

    # Write logs to standard live_stream.log file (fallback)
    stream_file = runtime.data_dir() / "live_stream.log"
    with open(stream_file, "a") as f:
        for log in extracted_logs:
            f.write(json.dumps(log) + "\n")

    # Insert to ClickHouse
    clickhouse_inserted = False
    if runtime.clickhouse_store().client:
        clickhouse_inserted = runtime.clickhouse_store().insert_logs(extracted_logs, tenant_id=tenant_id)

    # Publish to Redis
    try:
        for log in extracted_logs:
            await runtime.redis_client().publish(f"log_stream:{tenant_id}", json.dumps(log))
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
    clickhouse_inserted = False
    if runtime.clickhouse_store().client and clickhouse_rows:
        clickhouse_inserted = runtime.clickhouse_store().insert_traces(clickhouse_rows, tenant_id=tenant_id)

    return {
        "status": "success",
        "spans_ingested": len(db_spans),
        "clickhouse": clickhouse_inserted
    }
