from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import json
from pathlib import Path

from denoiser.storage.db import get_db, SavedQuery
from denoiser.api.auth import get_current_user, require_role, User
from denoiser.query.models import SavedQuerySchema, QueryCreateSchema, QueryRequestSchema
from denoiser.query.parser import parse_query, evaluate_in_memory, compile_to_sql
from denoiser.storage.clickhouse_store import ClickHouseStore

router = APIRouter(prefix="/query", tags=["query"])
clickhouse_store = ClickHouseStore()

DATA_DIR = Path("data")

@router.post("", response_model=Dict[str, Any])
def execute_query(payload: QueryRequestSchema, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Execute a log query. Uses ClickHouse if available, else falls back to in-memory scanning of data/live_stream.log
    """
    ast = parse_query(payload.query)
    
    if clickhouse_store.client:
        try:
            params = {'tenant_id': current_user.tenant_id}
            sql_where = compile_to_sql(ast, params)
            sql_where = f"tenant_id = {{tenant_id:String}} AND ({sql_where})"
            
            query = f"SELECT timestamp, source, level, message, raw_json FROM semantic_logs WHERE {sql_where} ORDER BY timestamp DESC LIMIT {payload.limit}"
            result = clickhouse_store.client.query(query, parameters=params)
            
            logs = []
            if result and result.result_rows:
                for row in result.result_rows:
                    row_dict = dict(zip(result.column_names, row))
                    log_entry = {}
                    if row_dict.get('raw_json'):
                        try:
                            log_entry = json.loads(row_dict['raw_json'])
                        except:
                            pass
                    # Merge structured fields
                    log_entry['timestamp'] = row_dict['timestamp']
                    log_entry['source'] = row_dict['source']
                    log_entry['level'] = row_dict['level']
                    log_entry['message'] = row_dict['message']
                    logs.append(log_entry)
                    
            return {"status": "success", "engine": "clickhouse", "logs": logs}
        except Exception as e:
            # Fallback to in-memory
            pass

    # In-memory fallback
    stream_file = DATA_DIR / "live_stream.log"
    logs = []
    if stream_file.exists():
        with open(stream_file, "r") as f:
            lines = f.readlines()
            # reverse to get newest first
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    log_dict = json.loads(line)
                    if evaluate_in_memory(ast, log_dict):
                        logs.append(log_dict)
                        if len(logs) >= payload.limit:
                            break
                except:
                    # plain text log
                    if evaluate_in_memory(ast, {"message": line}):
                        logs.append({"message": line})
                        if len(logs) >= payload.limit:
                            break
                            
    return {"status": "success", "engine": "in-memory", "logs": logs}


@router.get("/saved", response_model=List[SavedQuerySchema])
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
def delete_saved_query(query_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    sq = db.query(SavedQuery).filter(SavedQuery.id == query_id).first()
    if not sq:
        raise HTTPException(status_code=404, detail="Saved query not found")
    db.delete(sq)
    db.commit()
    return {"status": "deleted"}
