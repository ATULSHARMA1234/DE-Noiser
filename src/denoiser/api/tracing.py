from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Dict, Any

from denoiser.storage.db import get_db, Span
from denoiser.api.auth import get_current_user, require_role, User
from denoiser.tracing.otlp_collector import process_otlp_traces
from denoiser.tracing.models import TraceSchema, SpanSchema

router = APIRouter(prefix="/traces", tags=["tracing"])

@router.post("/v1/traces", summary="OTLP HTTP Ingest")
def ingest_traces(payload: Dict[Any, Any] = Body(...), db: Session = Depends(get_db)):
    """
    Ingest OpenTelemetry traces via HTTP JSON.
    Usually authenticated via a different mechanism for collectors, but we'll accept it raw for now.
    """
    try:
        process_otlp_traces(db, payload)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("", response_model=List[TraceSchema])
def list_traces(limit: int = 50, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    List trace aggregates. We group by trace_id.
    """
    # SQLite/PostgreSQL compatible grouping
    # Find all root spans (parent_span_id is None) to get trace metadata
    root_spans = db.query(Span).filter(Span.parent_span_id == None).order_by(Span.start_time.desc()).limit(limit).all()
    
    result = []
    for root in root_spans:
        # get all spans for this trace
        all_spans = db.query(Span).filter(Span.trace_id == root.trace_id).all()
        error_count = sum(1 for s in all_spans if s.status_code == "ERROR")
        
        # calculate total duration from root or min/max
        start = min(s.start_time for s in all_spans)
        end = max(s.end_time for s in all_spans)
        duration_ms = max(0, (end - start).total_seconds() * 1000.0)
        
        trace = TraceSchema(
            trace_id=root.trace_id,
            root_service=root.service_name,
            root_operation=root.operation_name,
            start_time=start,
            duration_ms=duration_ms,
            span_count=len(all_spans),
            error_count=error_count,
            spans=[] # don't include all spans in list view to save bandwidth
        )
        result.append(trace)
        
    return result

@router.get("/{trace_id}", response_model=TraceSchema)
def get_trace(trace_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Get full trace details including all spans for waterfall visualization.
    """
    all_spans = db.query(Span).filter(Span.trace_id == trace_id).order_by(Span.start_time.asc()).all()
    if not all_spans:
        raise HTTPException(status_code=404, detail="Trace not found")
        
    root = next((s for s in all_spans if s.parent_span_id is None), all_spans[0])
    error_count = sum(1 for s in all_spans if s.status_code == "ERROR")
    
    start = min(s.start_time for s in all_spans)
    end = max(s.end_time for s in all_spans)
    duration_ms = max(0, (end - start).total_seconds() * 1000.0)
    
    trace = TraceSchema(
        trace_id=root.trace_id,
        root_service=root.service_name,
        root_operation=root.operation_name,
        start_time=start,
        duration_ms=duration_ms,
        span_count=len(all_spans),
        error_count=error_count,
        spans=[SpanSchema.model_validate(s) for s in all_spans]
    )
    return trace
