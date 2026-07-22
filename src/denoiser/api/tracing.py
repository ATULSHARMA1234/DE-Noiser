import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

# SQLite dependencies just for OTLP collector signature if needed
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.storage.clickhouse_store import ClickHouseStore
from denoiser.storage.db import get_db
from denoiser.tracing.models import SpanSchema, TraceSchema
from denoiser.tracing.otlp_collector import process_otlp_traces

router = APIRouter(prefix="/traces", tags=["tracing"])

import contextlib
from datetime import UTC

from fastapi import Header

DATA_DIR = Path("data")
_clickhouse_store = ClickHouseStore()

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

        from denoiser.api.main import kafka_producer
        if kafka_producer:
            payload["_tenant_id"] = tenant_id
            msg_bytes = json.dumps(payload).encode('utf-8')
            await kafka_producer.send_and_wait("traces_topic", msg_bytes)
        else:
            process_otlp_traces(db, payload, tenant_id=tenant_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("", response_model=list[TraceSchema])
def list_traces(from_ts: int | None = None, to_ts: int | None = None, limit: int = 50, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    List trace aggregates. Uses ClickHouse if available, else falls back to SQLite data.
    """
    client = _clickhouse_store.client
    if client:
        try:
            # Group by trace_id to get root span data and trace metadata
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
                WHERE tenant_id = {{tenant_id:String}}
                { " AND start_time >= toDateTime64({from_ts:Float64}, 3, 'UTC')" if from_ts is not None else "" }
                { " AND start_time <= toDateTime64({to_ts:Float64}, 3, 'UTC')" if to_ts is not None else "" }
                GROUP BY trace_id
                ORDER BY start_time DESC
                LIMIT {limit}
            """

            params = {'tenant_id': current_user.tenant_id}
            if from_ts is not None:
                params['from_ts'] = from_ts / 1000.0
            if to_ts is not None:
                params['to_ts'] = to_ts / 1000.0

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
        ).filter(Span.tenant_id == current_user.tenant_id)
        
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
def get_trace(trace_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Get full trace details including all spans. Uses ClickHouse if available, else falls back to SQLite.
    """
    client = _clickhouse_store.client
    if client:
        try:
            sql = """
                SELECT *
                FROM semantic_traces
                WHERE tenant_id = {tenant_id:String} AND trace_id = {trace_id:String}
                ORDER BY start_time ASC
            """

            result = client.query(sql, parameters={'tenant_id': current_user.tenant_id, 'trace_id': trace_id})
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
        db_spans = db.query(Span).filter(Span.trace_id == trace_id, Span.tenant_id == current_user.tenant_id).order_by(Span.start_time.asc()).all()
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

