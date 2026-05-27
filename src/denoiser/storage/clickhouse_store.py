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
            # Ensure table exists
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
