import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

# SQLite dependencies just for OTLP collector signature if needed
from sqlalchemy.orm import Session

from denoiser import runtime
from denoiser.api.auth import User, require_role
from denoiser.api.pagination import MAX_PAGE_SIZE
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.storage.db import get_db
from denoiser.tracing.models import SpanSchema, TraceSchema
from denoiser.tracing.otlp_collector import process_otlp_traces
from denoiser.utils.time import iso_utc

router = APIRouter(prefix="/traces", tags=["tracing"])

import contextlib
from datetime import UTC, datetime, timedelta

from fastapi import Header

DATA_DIR = Path("data")


from sqlalchemy import case, func

from denoiser.storage.db import Span


@router.post("/v1/traces", summary="OTLP HTTP Ingest")
async def ingest_traces(
    payload: dict[Any, Any] = Body(...),
    db: Session = Depends(get_db),
    api_key: str = Header(None, alias="x-api-key")
):
    """
    Ingest OpenTelemetry traces via HTTP JSON.
    """
    try:
        from denoiser.storage.db import Tenant
        tenant = db.query(Tenant).filter(Tenant.api_key == api_key).first() if api_key else None
        # Default fallback for testing if no key is provided
        tenant_id = tenant.id if tenant else "default_tenant"

        producer = runtime.kafka_producer()
        if producer:
            payload["_tenant_id"] = tenant_id
            msg_bytes = json.dumps(payload).encode('utf-8')
            await producer.send_and_wait("traces_topic", msg_bytes)
        else:
            process_otlp_traces(db, payload, tenant_id=tenant_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/import", summary="Import traces from a stored trace file")
def import_traces_from_file(
    filename: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    """Load spans from a JSON trace file in the data directory.

    Traces could only arrive over OTLP from a live instrumented service, so the
    Traces tab was permanently empty for anyone holding an exported trace file —
    including the one this project ships. Accepts either the OTLP JSON envelope
    (``resourceSpans``) or the flat ``[{trace_id, spans: [...]}]`` export shape.
    """
    # Resolve inside DATA_DIR only — a filename is not a path the caller gets to
    # roam with.
    candidate = (DATA_DIR / Path(filename).name).resolve()
    if not str(candidate).startswith(str(DATA_DIR.resolve())) or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"Trace file not found: {filename}")

    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Could not read {filename}: {e}")

    tenant_id = str(current_user.tenant_id)

    if isinstance(payload, dict) and "resourceSpans" in payload:
        try:
            process_otlp_traces(db, payload, tenant_id=tenant_id)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to store spans: {e}")
        return {"status": "imported", "format": "otlp", "file": candidate.name}

    if not isinstance(payload, list):
        raise HTTPException(
            status_code=400,
            detail="Unrecognised trace file: expected an OTLP object or a list of traces",
        )

    rows: list[tuple] = []
    for trace in payload:
        if not isinstance(trace, dict):
            continue
        for span in trace.get("spans", []):
            start = _parse_span_time(span.get("start_time"))
            end = _parse_span_time(span.get("end_time"))
            if start is None:
                continue
            duration = span.get("duration_ms")
            if duration is None:
                duration = (end - start).total_seconds() * 1000.0 if end else 0.0
            if end is None:
                end = start + timedelta(milliseconds=float(duration))

            rows.append((
                span.get("trace_id") or trace.get("trace_id") or "",
                span.get("span_id") or "",
                span.get("parent_span_id") or "",
                span.get("service_name") or trace.get("root_service") or "unknown_service",
                span.get("operation_name") or trace.get("root_operation") or "",
                start,
                end,
                float(duration),
                (span.get("status_code") or "OK").upper(),
                json.dumps(span.get("attributes") or {}),
                json.dumps(span.get("events") or []),
            ))

    if not rows:
        raise HTTPException(status_code=400, detail=f"No spans found in {candidate.name}")

    if not runtime.clickhouse_store().insert_traces(rows, tenant_id=tenant_id):
        raise HTTPException(status_code=502, detail="Trace store rejected the spans")

    # Report the span of what was imported: an exported file usually carries its
    # original timestamps, so the traces can land outside the UI's current time
    # range and look like the import silently did nothing.
    starts = [r[5] for r in rows]
    return {
        "status": "imported",
        "format": "export",
        "file": candidate.name,
        "traces": len({r[0] for r in rows}),
        "spans": len(rows),
        "earliest": iso_utc(min(starts)),
        "latest": iso_utc(max(starts)),
    }


def _parse_span_time(value: Any):
    """Span timestamps as naive UTC, accepting ISO strings or epoch ms/seconds."""
    if value is None:
        return None
    if isinstance(value, int | float):
        seconds = value / 1000.0 if value > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


@router.get("", response_model=list[TraceSchema])
def list_traces(
    from_ts: int | None = None,
    to_ts: int | None = None,
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """
    List trace aggregates. Uses ClickHouse if available, else falls back to SQLite data.
    """
    store = runtime.clickhouse_store()
    client = store.client
    if client:
        try:
            # The tenant predicate and the time bounds come from the store —
            # the only module that knows how the trace table is partitioned, and
            # the only one that refuses to build a clause without a tenant.
            where, params = store.scope(
                current_user.tenant_id,
                from_ts=from_ts,
                to_ts=to_ts,
                time_column="start_time",
            )
            sql = f"""
                SELECT
                    trace_id,
                    any(service_name) AS root_service,
                    any(operation_name) AS root_operation,
                    min(start_time) AS start_time,
                    max(end_time) AS end_time,
                    count() AS span_count,
                    countIf(status_code = 'ERROR') AS error_count
                FROM semantic_traces
                WHERE {where}
                GROUP BY trace_id
                ORDER BY start_time DESC
                LIMIT {limit}
            """

            result = client.query(sql, parameters=params)

            traces = []
            for row in result.result_rows:
                row_dict = dict(zip(result.column_names, row, strict=False))

                start_t = row_dict['start_time']
                end_t = row_dict['end_time']
                duration_ms = max(0, (end_t - start_t).total_seconds() * 1000.0)

                trace = TraceSchema(
                    trace_id=row_dict['trace_id'],
                    root_service=row_dict['root_service'],
                    root_operation=row_dict['root_operation'],
                    start_time=start_t,
                    duration_ms=duration_ms,
                    span_count=row_dict['span_count'],
                    error_count=row_dict['error_count'],
                    spans=[]
                )
                traces.append(trace)

            return traces
        except Exception:
            pass  # Fall through to in-memory

    # SQLite fallback
    try:
        from datetime import datetime
        query = db.query(
            Span.trace_id,
            func.min(Span.start_time).label("start_time"),
            func.max(Span.end_time).label("end_time"),
            func.count(Span.id).label("span_count"),
            func.sum(case((Span.status_code == 'ERROR', 1), else_=0)).label("error_count")
        ).filter(scope.predicate(Span))
        
        if from_ts is not None:
            query = query.filter(Span.start_time >= datetime.fromtimestamp(from_ts / 1000.0, tz=UTC))
        if to_ts is not None:
            query = query.filter(Span.start_time <= datetime.fromtimestamp(to_ts / 1000.0, tz=UTC))
            
        # SQLite doesn't let us easily pull the FIRST service_name per trace without a subquery or window func. 
        # For fallback, we just pull aggregated info. Root service/operation can be looked up or approximated.
        # But wait, we can just join or do an extra query if we really want root spans. For now we will approximate
        # or do a slightly simpler group by.
        results = query.group_by(Span.trace_id).order_by(func.min(Span.start_time).desc()).limit(limit).all()
        
        filtered = []
        for row in results:
            # We don't have root_service in this simplified group_by, so we fetch the root span or just label it
            # To be efficient, let's just do a quick fetch of the root span for these trace_ids
            root_span = db.query(Span).filter(Span.trace_id == row.trace_id, Span.parent_span_id.is_(None)).first()
            if not root_span:
                # If no strict root span found, just pick any span for the trace
                root_span = db.query(Span).filter(Span.trace_id == row.trace_id).first()

            duration_ms = max(0, (row.end_time - row.start_time).total_seconds() * 1000.0) if row.end_time and row.start_time else 0
            filtered.append(TraceSchema(
                trace_id=row.trace_id,
                root_service=root_span.service_name if root_span else "unknown",
                root_operation=root_span.operation_name if root_span else "unknown",
                start_time=row.start_time,
                duration_ms=duration_ms,
                span_count=row.span_count,
                error_count=row.error_count or 0,
                spans=[]
            ))
            
        return filtered
    except Exception as e:
        print(f"SQLite fallback failed: {e}")
        return []

@router.get("/{trace_id}", response_model=TraceSchema)
def get_trace(
    trace_id: str,
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """
    Get full trace details including all spans. Uses ClickHouse if available, else falls back to SQLite.
    """
    store = runtime.clickhouse_store()
    client = store.client
    if client:
        try:
            where, params = store.scope(
                current_user.tenant_id,
                extra=["trace_id = {trace_id:String}"],
                bind={"trace_id": trace_id},
            )
            sql = f"""
                SELECT *
                FROM semantic_traces
                WHERE {where}
                ORDER BY start_time ASC
            """

            result = client.query(sql, parameters=params)
            if result.result_rows:
                spans = []
                for row in result.result_rows:
                    s_dict = dict(zip(result.column_names, row, strict=False))

                    attributes = {}
                    if s_dict.get('attributes'):
                        with contextlib.suppress(BaseException):
                            attributes = json.loads(s_dict['attributes'])

                    events = []
                    if s_dict.get('events'):
                        with contextlib.suppress(BaseException):
                            events = json.loads(s_dict['events'])

                    spans.append(SpanSchema(
                        trace_id=s_dict['trace_id'],
                        span_id=s_dict['span_id'],
                        parent_span_id=s_dict['parent_span_id'] or None,
                        service_name=s_dict['service_name'],
                        operation_name=s_dict['operation_name'],
                        start_time=s_dict['start_time'],
                        end_time=s_dict['end_time'],
                        duration_ms=s_dict['duration_ms'],
                        status_code=s_dict['status_code'],
                        attributes=attributes,
                        events=events
                    ))

                root = next((s for s in spans if not s.parent_span_id), spans[0])
                error_count = sum(1 for s in spans if s.status_code == 'ERROR')

                start = min(s.start_time for s in spans)
                end = max(s.end_time for s in spans)
                duration_ms = max(0, (end - start).total_seconds() * 1000.0)

                return TraceSchema(
                    trace_id=root.trace_id,
                    root_service=root.service_name,
                    root_operation=root.operation_name,
                    start_time=start,
                    duration_ms=duration_ms,
                    span_count=len(spans),
                    error_count=error_count,
                    spans=spans
                )
        except Exception:
            pass  # Fall through to in-memory

    # SQLite fallback
    try:
        db_spans = (
            scope.query(Span)
            .filter(Span.trace_id == trace_id)
            .order_by(Span.start_time.asc())
            .all()
        )
        if not db_spans:
            raise HTTPException(status_code=404, detail="Trace not found")
            
        spans = []
        for s in db_spans:
            spans.append(SpanSchema(
                trace_id=s.trace_id,
                span_id=s.span_id,
                parent_span_id=s.parent_span_id,
                service_name=s.service_name,
                operation_name=s.operation_name,
                start_time=s.start_time,
                end_time=s.end_time,
                duration_ms=s.duration_ms,
                status_code=s.status_code or "OK",
                attributes=s.attributes or {},
                events=s.events or []
            ))
            
        root = next((s for s in spans if not s.parent_span_id), spans[0])
        error_count = sum(1 for s in spans if s.status_code == 'ERROR')

        start = min(s.start_time for s in spans)
        end = max(s.end_time for s in spans)
        duration_ms = max(0, (end - start).total_seconds() * 1000.0)
        
        return TraceSchema(
            trace_id=root.trace_id,
            root_service=root.service_name,
            root_operation=root.operation_name,
            start_time=start,
            duration_ms=duration_ms,
            span_count=len(spans),
            error_count=error_count,
            spans=spans
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"SQLite fallback failed: {e}")
        raise HTTPException(status_code=404, detail="Trace not found")

