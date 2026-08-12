import json
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from denoiser import runtime
from denoiser.api.auth import verify_ingest_auth
from denoiser.storage.db import Span, get_db, insert_ignoring_duplicates

router = APIRouter(prefix="/v1", tags=["OTLP"])

#: Ceiling on spans in a single `/v1/traces` request. Well above what any
#: exporter sends in one batch — the OTel default is a few hundred — so it costs
#: a well-behaved client nothing and bounds what one authenticated caller can
#: make the API process hold at once.
MAX_SPANS_PER_REQUEST = int(os.getenv("OTLP_MAX_SPANS_PER_REQUEST", "10000"))


@router.post("/logs")
async def ingest_otlp_logs(request: Request, tenant_id: int = Depends(verify_ingest_auth)) -> dict[str, Any]:
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

    from denoiser.api.platform_settings import raw_log_storage_enabled, redact_batch

    # Before any sink sees it. This endpoint reached three of them — the raw
    # object-store copy, ClickHouse, and the Redis stream the live console
    # reads — with the record exactly as the customer sent it, while `/ingest`
    # redacted at its own boundary. Redaction was on, the operator believed it
    # was on, and it was: for the other path.
    #
    # This is the endpoint the README tells enterprises to use.
    extracted_logs = redact_batch(extracted_logs)

    # The redundant raw copy, through the shared sink rather than a per-pod
    # file — see denoiser.storage.raw_log_sink. Both implementations block, so
    # it runs off the event loop.
    if raw_log_storage_enabled():
        await run_in_threadpool(
            runtime.raw_log_sink().write, tenant_id, [json.dumps(log) for log in extracted_logs]
        )

    # Insert to ClickHouse. Redacted at the boundary above, like everything
    # else this request fans out to.
    clickhouse_configured = bool(runtime.clickhouse_store().client)
    clickhouse_inserted = False
    if clickhouse_configured:
        clickhouse_inserted = runtime.clickhouse_store().insert_logs(
            extracted_logs, tenant_id=tenant_id, redact=False
        )

    # Same contract as the trace endpoint above: a configured store that
    # refused the batch is a delivery failure, not a partial success, and the
    # exporter is the only party that still holds the records.
    if clickhouse_configured and not clickhouse_inserted:
        raise HTTPException(
            status_code=503,
            detail="Logs could not be written to the log store; retry the batch.",
        )

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
async def ingest_otlp_traces(request: Request, db: Session = Depends(get_db), tenant_id: int = Depends(verify_ingest_auth)) -> dict[str, Any]:
    """
    Accepts OpenTelemetry standard JSON traces payload (resourceSpans) and ingestion.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    resource_spans = body.get("resourceSpans", [])

    # The body, the parsed JSON, and both row lists coexist in memory for the
    # duration of the request. `BodySizeLimitMiddleware` bounds this by
    # Content-Length, but a chunked request without one is passed through by
    # design — and unlike the ingest and upload routes named in that decision,
    # this one had no validator of its own.
    #
    # OTLP exporters batch; the default is a few hundred spans. A ceiling well
    # above that costs a well-behaved client nothing.
    span_count = sum(
        len(scope_span.get("spans", []))
        for r_span in resource_spans
        for scope_span in r_span.get("scopeSpans", [])
    )
    if span_count > MAX_SPANS_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{span_count} spans in one request exceeds the limit of "
                f"{MAX_SPANS_PER_REQUEST}. Reduce your exporter's batch size."
            ),
        )

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
                    # The owner, which this row went without. `tenant_id` is an
                    # integer column and it was simply never set, so every span
                    # ingested here belonged to nobody: hidden from tenant-scoped
                    # reads, counted as zero by metering, and left behind by the
                    # customer's own erasure. ClickHouse got the tenant on the
                    # next line down the whole time.
                    tenant_id=tenant_id,
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

                # ClickHouse tuple row mapping. `parent_span_id` is a
                # non-Nullable String there, and a root span has no parent, so
                # the absent value has to arrive as "" rather than None — one
                # None failed the insert for the whole batch, which meant every
                # trace containing a root span (that is, every trace) was
                # dropped. The read path in api.tracing turns "" back into None.
                clickhouse_rows.append((
                    span_data.get("traceId"),
                    span_data.get("spanId"),
                    span_data.get("parentSpanId") or "",
                    service_name,
                    span_data.get("name"),
                    start_dt,
                    end_dt,
                    duration_ms,
                    status_code,
                    json.dumps(attributes),
                    json.dumps(span_data.get("events", []))
                ))

    # ClickHouse is the system of record for spans, so it goes first. When it
    # refuses the batch the request fails without having written anything, and
    # the exporter's retry starts from a clean slate.
    #
    # The other order — commit to Postgres, then 503 — is what this endpoint
    # used to do, and it turned a ClickHouse outage into unbounded Postgres
    # growth: an OTLP exporter retries a 503 roughly six times over a five
    # minute backoff, and every attempt committed another full copy of the
    # batch before failing.
    clickhouse_configured = bool(runtime.clickhouse_store().client)
    clickhouse_inserted = False
    if clickhouse_configured and clickhouse_rows:
        clickhouse_inserted = runtime.clickhouse_store().insert_traces(clickhouse_rows, tenant_id=tenant_id)

    # When ClickHouse is the trace store, a failed insert is a lost trace: the
    # listing reads ClickHouse and answers `[]` without erroring, so the spans
    # sitting in Postgres are never shown. Reporting 200 here told the exporter
    # its batch was delivered and there was nothing left to retry. 503 is the
    # status an OTLP exporter retries on.
    if clickhouse_configured and clickhouse_rows and not clickhouse_inserted:
        raise HTTPException(
            status_code=503,
            detail="Spans could not be written to the trace store; retry the batch.",
        )

    # The Postgres mirror, through an insert that ignores rows already present.
    # Ordering alone does not make the endpoint idempotent — a retry after a
    # network failure that dropped our 200 replays the same spans — so the
    # uniqueness of (tenant, trace, span) is enforced by the database and a
    # replay is a no-op rather than a duplicate.
    newly_stored = insert_ignoring_duplicates(db, Span, db_spans)
    db.commit()

    return {
        "status": "success",
        # What the caller sent and we accepted. Unchanged by deduplication: a
        # replay is still a fully accepted batch, and an exporter that read a
        # smaller number here would have no way to act on it.
        "spans_ingested": len(db_spans),
        # How many of those were new. `spans_ingested - spans_stored` is the
        # replayed remainder, which is a useful thing for an operator chasing a
        # misconfigured exporter to be able to see.
        "spans_stored": newly_stored,
        "clickhouse": clickhouse_inserted
    }
