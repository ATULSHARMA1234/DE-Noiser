import json
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

from fastapi import Header


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
def list_traces(limit: int = 50, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    List trace aggregates fetched directly from ClickHouse.
    """
    store = ClickHouseStore()
    client = store.client
    if not client:
        raise HTTPException(status_code=500, detail="ClickHouse not available")

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
        GROUP BY trace_id
        ORDER BY start_time DESC
        LIMIT {limit}
    """

    try:
        result = client.query(sql, parameters={'tenant_id': current_user.tenant_id})

        traces = []
        for row in result.result_rows:
            row_dict = dict(zip(result.column_names, row, strict=False))

            # ClickHouse datetime objects can be converted
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
                spans=[] # exclude spans for list view
            )
            traces.append(trace)

        return traces
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{trace_id}", response_model=TraceSchema)
def get_trace(trace_id: str, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Get full trace details including all spans from ClickHouse.
    """
    store = ClickHouseStore()
    client = store.client
    if not client:
        raise HTTPException(status_code=500, detail="ClickHouse not available")

    sql = """
        SELECT *
        FROM semantic_traces
        WHERE tenant_id = {tenant_id:String} AND trace_id = {trace_id:String}
        ORDER BY start_time ASC
    """

    try:
        result = client.query(sql, parameters={'tenant_id': current_user.tenant_id, 'trace_id': trace_id})
        if not result.result_rows:
            raise HTTPException(status_code=404, detail="Trace not found")

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

            # Parse datetime fields handled by clickhouse-connect
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

        trace = TraceSchema(
            trace_id=root.trace_id,
            root_service=root.service_name,
            root_operation=root.operation_name,
            start_time=start,
            duration_ms=duration_ms,
            span_count=len(spans),
            error_count=error_count,
            spans=spans
        )
        return trace

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
