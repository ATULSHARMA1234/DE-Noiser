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
                    tenant_id String,
                    timestamp DateTime64(3, 'UTC'),
                    source String,
                    level String,
                    message String,
                    raw_json String
                ) ENGINE = MergeTree()
                ORDER BY (tenant_id, source, timestamp)
            """)

            self.client.command("""
                CREATE TABLE IF NOT EXISTS semantic_traces (
                    tenant_id String,
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
                ORDER BY (tenant_id, service_name, start_time)
            """)

            logger.info(f"Connected to ClickHouse at {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to connect to ClickHouse: {e}")
            self.client = None

    def cleanup_old_data(self, tenant_id: str, days_to_keep: int):
        """
        Phase 26: Data Tiering. Deletes logs and traces older than `days_to_keep` for a specific tenant.
        """
        if not self.client:
            return
        try:
            # Delete old logs
            self.client.command(f"""
                ALTER TABLE semantic_logs
                DELETE WHERE tenant_id = '{tenant_id}' AND timestamp < now() - INTERVAL {days_to_keep} DAY
            """)
            # Delete old traces
            self.client.command(f"""
                ALTER TABLE semantic_traces
                DELETE WHERE tenant_id = '{tenant_id}' AND start_time < now() - INTERVAL {days_to_keep} DAY
            """)
            logger.info(f"Cleaned up data older than {days_to_keep} days for tenant {tenant_id}")
        except Exception as e:
            logger.error(f"Failed to cleanup old data for tenant {tenant_id}: {e}")

    def insert_logs(self, logs: list[dict[str, Any]], tenant_id: str):
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
                dt = datetime.fromtimestamp(ts, UTC) if isinstance(ts, (int, float)) else datetime.now(UTC)

                data.append((
                    tenant_id,
                    dt,
                    log.get("source", "unknown"),
                    log.get("level", "INFO"),
                    log.get("message", str(log)),
                    json.dumps(log)
                ))

            self.client.insert('semantic_logs', data, column_names=['tenant_id', 'timestamp', 'source', 'level', 'message', 'raw_json'])
            return True
        except Exception as e:
            logger.error(f"Failed to insert into ClickHouse: {e}")
            return False

    def insert_traces(self, traces_data: list[tuple], tenant_id: str):
        """Insert processed OTLP spans into ClickHouse"""
        if not self.client:
            return False

        try:
            # Insert tenant_id to the beginning of each tuple
            traces_data_with_tenant = [(tenant_id, *row) for row in traces_data]

            self.client.insert('semantic_traces', traces_data_with_tenant, column_names=[
                'tenant_id', 'trace_id', 'span_id', 'parent_span_id', 'service_name',
                'operation_name', 'start_time', 'end_time', 'duration_ms',
                'status_code', 'attributes', 'events'
            ])
            return True
        except Exception as e:
            logger.error(f"Failed to insert traces into ClickHouse: {e}")
            return False

    def query_logs(self, query_string: str = "", limit: int = 100, tenant_id: str = ""):
        """Execute a parsed Log Query Language (LQL) search against ClickHouse."""
        if not self.client:
            return []

        from denoiser.query.parser import compile_to_sql, parse_query

        ast = parse_query(query_string)
        params = {}
        sql_where = compile_to_sql(ast, params)

        if tenant_id:
            sql_where = f"tenant_id = {{tenant_id:String}} AND ({sql_where})"
            params['tenant_id'] = tenant_id

        sql = "SELECT * FROM semantic_logs"
        sql += f" WHERE {sql_where}"

        sql += f" ORDER BY timestamp DESC LIMIT {limit}"

        try:
            result = self.client.query(sql, parameters=params)
            return [dict(zip(result.column_names, row, strict=False)) for row in result.result_rows]
        except Exception as e:
            logger.error(f"Failed to query ClickHouse: {e}")
            return []
