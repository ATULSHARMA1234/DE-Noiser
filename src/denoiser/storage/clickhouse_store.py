import json
import os
import time
from datetime import UTC
from typing import Any

from denoiser.logging import get_logger

logger = get_logger(__name__)

class ClickHouseStore:
    def __init__(self):
        self.host = os.getenv("CLICKHOUSE_HOST", "localhost")
        self.port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
        self.username = os.getenv("CLICKHOUSE_USER", "default")
        self.password = os.getenv("CLICKHOUSE_PASSWORD", "")
        self.database = os.getenv("CLICKHOUSE_DB", "default")
        self.client = None
        self._init_client()

    def _init_client(self):
        try:
            import clickhouse_connect

            self.client = clickhouse_connect.get_client(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                database=self.database
            )
            # Ensure tables exist
            self.client.command("""
                CREATE TABLE IF NOT EXISTS semantic_logs (
                    timestamp DateTime64(3, 'UTC'),
                    source String,
                    level String,
                    message String,
                    raw_json String
                ) ENGINE = MergeTree()
                ORDER BY (source, timestamp)
            """)
            
            self.client.command("""
                CREATE TABLE IF NOT EXISTS semantic_traces (
                    trace_id String,
                    span_id String,
                    parent_span_id String,
                    service_name String,
                    operation_name String,
                    start_time DateTime64(3, 'UTC'),
                    end_time DateTime64(3, 'UTC'),
                    duration_ms Float64,
                    status_code String,
                    attributes String,
                    events String
                ) ENGINE = MergeTree()
                ORDER BY (service_name, start_time)
            """)
            
            logger.info(f"Connected to ClickHouse at {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to connect to ClickHouse: {e}")
            self.client = None

    def insert_logs(self, logs: list[dict[str, Any]]):
        """Dual-write logs to ClickHouse"""
        if not self.client:
            return False

        try:
            # Flatten log dicts to tuples matching schema
            data = []
            for log in logs:
                ts = log.get("timestamp", time.time())
                # If timestamp is seconds, convert to datetime object
                from datetime import datetime
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts, UTC)
                else:
                    dt = datetime.now(UTC)

                data.append((
                    dt,
                    log.get("source", "unknown"),
                    log.get("level", "INFO"),
                    log.get("message", str(log)),
                    json.dumps(log)
                ))

            self.client.insert('semantic_logs', data, column_names=['timestamp', 'source', 'level', 'message', 'raw_json'])
            return True
        except Exception as e:
            logger.error(f"Failed to insert into ClickHouse: {e}")
            return False

    def insert_traces(self, traces_data: list[tuple]):
        """Insert processed OTLP spans into ClickHouse"""
        if not self.client:
            return False
            
        try:
            self.client.insert('semantic_traces', traces_data, column_names=[
                'trace_id', 'span_id', 'parent_span_id', 'service_name', 
                'operation_name', 'start_time', 'end_time', 'duration_ms', 
                'status_code', 'attributes', 'events'
            ])
            return True
        except Exception as e:
            logger.error(f"Failed to insert traces into ClickHouse: {e}")
            return False

    def query_logs(self, query_string: str = "", limit: int = 100):
        """Execute a parsed Log Query Language (LQL) search against ClickHouse."""
        if not self.client:
            return []
            
        where_clauses = []
        parameters = {}
        
        if query_string:
            tokens = query_string.split(" AND ")
            for i, token in enumerate(tokens):
                if ":" in token:
                    key, val = token.split(":", 1)
                    key = key.strip()
                    val = val.strip()
                    if key in ["source", "level"]:
                        where_clauses.append(f"{key} = {{val_{i}:String}}")
                        parameters[f"val_{i}"] = val
                    else:
                        # JSON extraction for custom attributes
                        where_clauses.append(f"JSONExtractString(raw_json, '{key}') = {{val_{i}:String}}")
                        parameters[f"val_{i}"] = val
                else:
                    # Free text search in message
                    where_clauses.append(f"message ILIKE {{val_{i}:String}}")
                    parameters[f"val_{i}"] = f"%{token.strip()}%"
                    
        sql = "SELECT * FROM semantic_logs"
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
            
        sql += f" ORDER BY timestamp DESC LIMIT {limit}"
        
        try:
            result = self.client.query(sql, parameters=parameters)
            return [dict(zip(result.column_names, row)) for row in result.result_rows]
        except Exception as e:
            logger.error(f"Failed to query ClickHouse: {e}")
            return []
