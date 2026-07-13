import contextlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.query.models import QueryCreateSchema, QueryRequestSchema, SavedQuerySchema
from denoiser.query.parser import compile_to_sql, evaluate_in_memory, parse_plain_text_log, parse_query
from denoiser.storage.clickhouse_store import ClickHouseStore
from denoiser.storage.db import SavedQuery, get_db

router = APIRouter(prefix="/query", tags=["query"])
clickhouse_store = ClickHouseStore()

DATA_DIR = Path("data")

def _get_all_memory_logs(from_ts: int | None = None, to_ts: int | None = None, file_name: str | None = None) -> list[dict]:
    """Helper to read and parse all .log files in DATA_DIR for in-memory fallbacks.
    If file_name is given, only that specific file is scanned.
    Each log entry is tagged with '_file' = the basename of its source file.
    """
    logs = []
    if not DATA_DIR.exists():
        return logs

    if file_name:
        # Only scan the requested file
        target = DATA_DIR / file_name
        log_files = [target] if target.exists() else []
    else:
        log_files = list(DATA_DIR.glob("*.log"))

    for log_file in log_files:
        fname = log_file.name
        try:
            with open(log_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        log_dict = json.loads(line)
                    except ValueError:
                        # Fallback to plain text parser
                        log_dict = parse_plain_text_log(line)
                        if not log_dict:
                            continue
                    
                    log_ts = log_dict.get("timestamp")
                    # Check time bounds
                    if log_ts is not None:
                        if from_ts is not None and log_ts * 1000 < from_ts:
                            continue
                        if to_ts is not None and log_ts * 1000 > to_ts:
                            continue

                    # Tag with the originating file name
                    log_dict["_file"] = fname
                    if not log_dict.get("source"):
                        # Derive a human-friendly source from the filename
                        log_dict["source"] = fname.replace(".log", "")
                            
                    logs.append(log_dict)
        except Exception:
            pass
            
    # Sort descending by timestamp
    logs.sort(key=lambda x: x.get("timestamp", 0) or 0, reverse=True)
    return logs


@router.get("/log-files")
def list_log_files(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """List all .log files available in the data directory."""
    files = []
    if DATA_DIR.exists():
        for f in sorted(DATA_DIR.glob("*.log")):
            files.append({
                "name": f.name,
                "label": f.name.replace(".log", "").replace("_", " ").title(),
                "size_bytes": f.stat().st_size
            })
    return {"files": files}


@router.post("", response_model=dict[str, Any])
def execute_query(payload: QueryRequestSchema, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Execute a log query. Uses ClickHouse if available, else falls back to in-memory scanning of data/*.log
    """
    ast = parse_query(payload.query)

    if clickhouse_store.client:
        try:
            params = {'tenant_id': str(current_user.tenant_id)}
            sql_where = compile_to_sql(ast, params)
            sql_where = f"tenant_id = {{tenant_id:String}} AND ({sql_where})"

            if payload.from_ts is not None:
                sql_where += " AND timestamp >= toDateTime64({from_ts:Float64}, 3, 'UTC')"
                params['from_ts'] = payload.from_ts / 1000.0
            if payload.to_ts is not None:
                sql_where += " AND timestamp <= toDateTime64({to_ts:Float64}, 3, 'UTC')"
                params['to_ts'] = payload.to_ts / 1000.0

            if payload.group_by == 'pattern':
                query = f"""
                    SELECT 
                        count() as count,
                        replaceRegexpAll(message, '([0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}}|[0-9]+)', '*') as pattern
                    FROM semantic_logs WHERE {sql_where} GROUP BY pattern ORDER BY count DESC LIMIT {payload.limit}
                """
            else:
                query = f"SELECT timestamp, source, level, message, raw_json FROM semantic_logs WHERE {sql_where} ORDER BY timestamp DESC LIMIT {payload.limit}"
            result = clickhouse_store.client.query(query, parameters=params)

            logs = []
            if result and result.result_rows:
                if payload.group_by == 'pattern':
                    for row in result.result_rows:
                        logs.append({
                            "count": row[0],
                            "pattern": row[1]
                        })
                else:
                    for row in result.result_rows:
                        row_dict = dict(zip(result.column_names, row, strict=False))
                        log_entry = {}
                        if row_dict.get('raw_json'):
                            with contextlib.suppress(BaseException):
                                log_entry = json.loads(row_dict['raw_json'])
                        # Merge structured fields
                        log_entry['timestamp'] = row_dict['timestamp']
                        log_entry['source'] = row_dict['source']
                        log_entry['level'] = row_dict['level']
                        log_entry['message'] = row_dict['message']
                        logs.append(log_entry)

            return {"status": "success", "engine": "clickhouse", "logs": logs}
        except Exception:
            # Fallback to in-memory
            pass

    # In-memory fallback
    all_logs = _get_all_memory_logs(payload.from_ts, payload.to_ts, payload.file_name)
    matched_logs = [log for log in all_logs if evaluate_in_memory(ast, log)]
    
    if payload.group_by == 'pattern':
        import re
        from collections import defaultdict
        pattern_counts = defaultdict(int)
        for log in matched_logs:
            msg = log.get("message", "")
            # mask UUIDs and numbers
            pattern = re.sub(r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9]+)', '*', msg)
            pattern_counts[pattern] += 1
            
        logs = [{"count": c, "pattern": p} for p, c in sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True)[:payload.limit]]
    else:
        logs = matched_logs[:payload.limit]

    return {"status": "success", "engine": "in-memory", "logs": logs}


@router.get("/facets")
def get_facets(from_ts: int | None = None, to_ts: int | None = None, file_name: str | None = None, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Get facet counts for source and level.
    """
    if clickhouse_store.client:
        try:
            facets = clickhouse_store.get_facets(tenant_id=current_user.tenant_id, from_ts=from_ts, to_ts=to_ts)
            return {"status": "success", "facets": facets}
        except Exception:
            pass
            
    # In-memory fallback
    all_logs = _get_all_memory_logs(from_ts, to_ts, file_name)
    source_counts = {}
    level_counts = {}
    for log in all_logs:
        src = log.get("source", "unknown")
        lvl = log.get("level", "INFO")
        source_counts[src] = source_counts.get(src, 0) + 1
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
        
    facets = {
        "source": [{"value": k, "count": v} for k, v in sorted(source_counts.items(), key=lambda x: x[1], reverse=True)[:20]],
        "level": [{"value": k, "count": v} for k, v in sorted(level_counts.items(), key=lambda x: x[1], reverse=True)[:20]]
    }
    return {"status": "success", "facets": facets}


@router.post("/histogram")
def get_histogram(payload: QueryRequestSchema, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Get log counts bucketed by time.
    """
    if clickhouse_store.client:
        try:
            buckets = clickhouse_store.get_histogram(
                query_string=payload.query,
                tenant_id=current_user.tenant_id,
                from_ts=payload.from_ts,
                to_ts=payload.to_ts
            )
            return {"status": "success", "buckets": buckets}
        except Exception:
            pass

    # In-memory fallback
    ast = parse_query(payload.query)
    all_logs = _get_all_memory_logs(payload.from_ts, payload.to_ts, payload.file_name)
    matched_logs = [log for log in all_logs if evaluate_in_memory(ast, log)]
    
    # Bucket by 1 hour (default) or scaled based on range
    interval_ms = 3600 * 1000
    if payload.from_ts and payload.to_ts:
        diff_hours = (payload.to_ts - payload.from_ts) / 3600000
        if diff_hours <= 1:
            interval_ms = 60 * 1000 # 1 min
        elif diff_hours <= 24:
            interval_ms = 15 * 60 * 1000 # 15 min
        elif diff_hours <= 24 * 7:
            interval_ms = 3600 * 1000 # 1 hr
        else:
            interval_ms = 24 * 3600 * 1000 # 1 day
            
    buckets = {}
    for log in matched_logs:
        ts = log.get("timestamp")
        if not ts:
            continue
        ts_ms = ts * 1000
        bucket_ts = (ts_ms // interval_ms) * interval_ms
        level = log.get("level", "INFO")
        
        if bucket_ts not in buckets:
            buckets[bucket_ts] = {"timestamp": bucket_ts, "count": 0}
        buckets[bucket_ts]["count"] += 1
        buckets[bucket_ts][level] = buckets[bucket_ts].get(level, 0) + 1
        
    sorted_buckets = [buckets[k] for k in sorted(buckets.keys())]
    return {"status": "success", "buckets": sorted_buckets}

@router.get("/saved", response_model=list[SavedQuerySchema])
def list_saved_queries(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    queries = db.query(SavedQuery).order_by(SavedQuery.created_at.desc()).all()
    return queries


@router.post("/saved", response_model=SavedQuerySchema)
def create_saved_query(payload: QueryCreateSchema, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    sq = SavedQuery(
        name=payload.name,
        query_text=payload.query_text,
        user_id=current_user.id
    )
    db.add(sq)
    db.commit()
    db.refresh(sq)
    return sq


@router.delete("/saved/{query_id}")
def delete_saved_query(query_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    sq = db.query(SavedQuery).filter(SavedQuery.id == query_id).first()
    if not sq:
        raise HTTPException(status_code=404, detail="Saved query not found")
    db.delete(sq)
    db.commit()
    return {"status": "deleted"}

