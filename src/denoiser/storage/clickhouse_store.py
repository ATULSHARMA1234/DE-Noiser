import json
import os
from datetime import UTC, datetime
from typing import Any

from denoiser.logging import get_logger

logger = get_logger(__name__)

# Where a log's originating service can be found, in priority order.
#
# Nothing in the wild sends a bare "source" key. Fluent Bit's Kubernetes filter
# nests container_name under "kubernetes", OpenTelemetry uses the dotted
# "service.name", Vector and most JSON loggers use "service". Reading only
# "source" meant every log from every real shipper landed as "unknown", which
# silently removes the per-service dimension that topology, causal correlation
# and cross-service spike detection are all built on — the clustering still
# works, it just has nothing to correlate across.
#
# Dotted entries are tried as a literal flat key first, then as a nested path,
# because shippers disagree about whether they flatten.
SOURCE_KEYS: tuple[str, ...] = (
    "source",
    "service",
    "service_name",
    "service.name",                 # OpenTelemetry resource attribute
    "kubernetes.container_name",    # Fluent Bit kubernetes filter
    "kubernetes.labels.app",
    "container_name",               # Docker / Fluentd docker driver
    "app",
    "logger_name",
)

UNKNOWN_SOURCE = "unknown"


def _lookup(log: dict[str, Any], key: str) -> Any:
    """Read ``key`` as a flat key, falling back to a nested dotted path."""
    if key in log:
        return log[key]

    if "." not in key:
        return None

    node: Any = log
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def resolve_source(log: dict[str, Any]) -> str:
    """The service a log came from, or ``unknown`` when nothing identifies it."""
    for key in SOURCE_KEYS:
        value = _lookup(log, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        # Numeric or otherwise non-string identifiers are still better than
        # nothing, but skip containers and lists — those are structure, not a name.
        if value is not None and not isinstance(value, dict | list):
            text = str(value).strip()
            if text:
                return text
    return UNKNOWN_SOURCE


# Where a log's severity can be found, in priority order — same problem as the
# source: only "level" was read, so OpenTelemetry ("severity_text"), syslog-
# derived shippers ("severity") and anything using "log.level" (ECS) all fell
# through to the INFO default. A real ERROR silently filed as INFO corrupts the
# severity histogram, incident ranking and SLO error-budget maths.
LEVEL_KEYS: tuple[str, ...] = (
    "level",
    "severity_text",   # OpenTelemetry
    "severity",        # syslog-derived, many JSON loggers
    "log.level",       # Elastic Common Schema
    "loglevel",
    "levelname",       # Python logging
    "status",
)

DEFAULT_LEVEL = "INFO"

# Aliases map onto the vocabulary the rest of the platform already uses —
# DEBUG, INFO, WARN, ERROR, FATAL — not a "more standard" WARNING/CRITICAL set.
# The Explore severity histogram hardcodes LEVEL_ORDER/LEVEL_COLOR on exactly
# these five names, so a value outside them renders colorless and drops out of
# the stack. Match the surrounding code rather than fight it.
_LEVEL_ALIASES: dict[str, str] = {
    "TRACE": "DEBUG", "DEBUG": "DEBUG", "DBG": "DEBUG",
    "INFO": "INFO", "INFORMATION": "INFO", "INFORMATIONAL": "INFO", "NOTICE": "INFO",
    "WARN": "WARN", "WARNING": "WARN",
    "ERR": "ERROR", "ERROR": "ERROR",
    "CRIT": "FATAL", "CRITICAL": "FATAL", "FATAL": "FATAL",
    "ALERT": "FATAL", "EMERGENCY": "FATAL", "EMERG": "FATAL", "PANIC": "FATAL",
    # Syslog numeric severities (RFC 5424): 0 emerg … 7 debug.
    "0": "FATAL", "1": "FATAL", "2": "FATAL", "3": "ERROR",
    "4": "WARN", "5": "INFO", "6": "INFO", "7": "DEBUG",
}


def resolve_level(log: dict[str, Any]) -> str:
    """The canonical severity of a log, or ``INFO`` when nothing specifies one."""
    for key in LEVEL_KEYS:
        value = _lookup(log, key)
        if value is None or isinstance(value, dict | list):
            continue
        raw = str(value).strip()
        if not raw:
            continue
        return _LEVEL_ALIASES.get(raw.upper(), raw.upper())
    return DEFAULT_LEVEL


# Where a log's event time can be found, in priority order. Same "only one key
# was read" problem as source and level: insert only looked at "timestamp", so
# Elastic's "@timestamp", the Docker json-file driver's "time", OTel's
# "timeUnixNano" and Fluent Bit's "date" all fell through to ingestion
# wall-clock — which detaches the stored time from the event and corrupts every
# time-range query and the volume histogram.
TIMESTAMP_KEYS: tuple[str, ...] = (
    "timestamp",
    "@timestamp",           # Elastic Common Schema
    "time",                 # Docker json-file driver
    "ts",
    "timeunixnano",         # OpenTelemetry (matched case-insensitively below)
    "observedtimeunixnano",
    "eventtime",
    "date",                 # Fluent Bit
)


def _from_epoch(value: float) -> datetime | None:
    """Epoch number to a UTC datetime, detecting the unit by magnitude.

    Shippers send epochs in seconds, milliseconds (JavaScript Date.now, many
    JSON loggers), microseconds, or nanoseconds (OpenTelemetry). Feeding a
    millisecond epoch to fromtimestamp as if it were seconds does not just store
    the wrong time — it raises ("year 56531 is out of range"), and because that
    happens inside insert_logs' batch loop it fails the whole batch. In the
    at-least-once worker a permanently failing batch is a poison pill that wedges
    the partition, so one JavaScript service can stop ingestion outright.

    Thresholds are chosen so a real timestamp (years ~2001-2100) is never
    misread: seconds top out around 4.1e9, well below the 1e11 boundary.
    """
    magnitude = abs(value)
    if magnitude >= 1e17:
        value /= 1e9    # nanoseconds
    elif magnitude >= 1e14:
        value /= 1e6    # microseconds
    elif magnitude >= 1e11:
        value /= 1e3    # milliseconds

    try:
        return datetime.fromtimestamp(value, UTC)
    except (ValueError, OSError, OverflowError):
        return None


def coerce_timestamp(value: Any) -> datetime | None:
    """A single timestamp field value to a UTC datetime, or None if unparseable.

    Never raises: a value it cannot understand returns None so the caller can
    fall back, rather than taking down the batch it belongs to.
    """
    # bool is a subclass of int; a truthy flag is not an epoch.
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return _from_epoch(float(value))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # A bare number in a string is still an epoch.
        try:
            return _from_epoch(float(s))
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        # A naive ISO string is assumed to be UTC rather than left tz-less,
        # so ClickHouse does not reinterpret it in the server's local zone.
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def resolve_timestamp(log: dict[str, Any]) -> datetime:
    """The event time of a log, or ingestion wall-clock when none is present."""
    lowered = {k.lower(): v for k, v in log.items()} if log else {}
    for key in TIMESTAMP_KEYS:
        value = _lookup(log, key)
        if value is None:
            value = lowered.get(key)  # case-insensitive (timeUnixNano etc.)
        if value is None or isinstance(value, dict | list):
            continue
        dt = coerce_timestamp(value)
        if dt is not None:
            return dt
    return datetime.now(UTC)


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
            # tenant_id is bound as a parameter (never string-interpolated) so a
            # crafted tenant id cannot break out of the WHERE clause. days is a
            # numeric interval, which ClickHouse won't accept as a bound value,
            # so it is hard-cast to int instead.
            days = int(days_to_keep)
            params = {"tenant_id": str(tenant_id)}
            # Delete old logs
            self.client.command(
                "ALTER TABLE semantic_logs DELETE WHERE tenant_id = {tenant_id:String} "
                f"AND timestamp < now() - INTERVAL {days} DAY",
                parameters=params,
            )
            # Delete old traces
            self.client.command(
                "ALTER TABLE semantic_traces DELETE WHERE tenant_id = {tenant_id:String} "
                f"AND start_time < now() - INTERVAL {days} DAY",
                parameters=params,
            )
            logger.info(f"Cleaned up data older than {days} days for tenant {tenant_id}")
        except Exception as e:
            logger.error(f"Failed to cleanup old data for tenant {tenant_id}: {e}")

    def insert_logs(self, logs: list[dict[str, Any]], tenant_id: str):
        """Dual-write logs to ClickHouse"""
        if not self.client:
            return False

        try:
            # tenant_id is a String column in ClickHouse; callers may pass an int
            # tenant id (e.g. Tenant.id). Coerce so the binary insert doesn't crash.
            tenant_id = str(tenant_id)
            # Flatten log dicts to tuples matching schema
            data = []
            for log in logs:
                dt = resolve_timestamp(log)

                data.append((
                    tenant_id,
                    dt,
                    resolve_source(log),
                    resolve_level(log),
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
            # tenant_id is a String column; callers may pass an int Tenant.id.
            tenant_id = str(tenant_id)
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

    def query_logs(self, query_string: str = "", limit: int = 100, tenant_id: str = "", from_ts: int | None = None, to_ts: int | None = None, group_by: str | None = None):
        """Execute a parsed Log Query Language (LQL) search against ClickHouse."""
        if not self.client:
            return []

        from denoiser.query.parser import compile_to_sql, parse_query

        ast = parse_query(query_string)
        params = {}
        sql_where = compile_to_sql(ast, params)

        if tenant_id:
            sql_where = f"tenant_id = {{tenant_id:String}} AND ({sql_where})"
            params['tenant_id'] = str(tenant_id)

        if from_ts is not None:
            sql_where += " AND timestamp >= toDateTime64({from_ts:Float64}, 3, 'UTC')"
            params['from_ts'] = from_ts / 1000.0
        if to_ts is not None:
            sql_where += " AND timestamp <= toDateTime64({to_ts:Float64}, 3, 'UTC')"
            params['to_ts'] = to_ts / 1000.0

        sql = "SELECT * FROM semantic_logs"
        
        if group_by == 'pattern':
            sql = """
                SELECT 
                    count() as count,
                    replaceRegexpAll(message, '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9]+)', '*') as pattern
                FROM semantic_logs
            """
            
        sql += f" WHERE {sql_where}"

        if group_by == 'pattern':
            sql += f" GROUP BY pattern ORDER BY count DESC LIMIT {limit}"
        else:
            sql += f" ORDER BY timestamp DESC LIMIT {limit}"

        try:
            result = self.client.query(sql, parameters=params)
            return [dict(zip(result.column_names, row, strict=False)) for row in result.result_rows]
        except Exception as e:
            logger.error(f"Failed to query ClickHouse: {e}")
            return []

    def aggregate_metric(
        self,
        query_string: str = "",
        aggregation: str = "count",
        tenant_id: str = "",
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> float:
        """Compute a real metric value over the ingested logs for a MetricRule.

        ``count`` is the well-defined default. For sum/avg/max/min there is no
        rule-level target field, so we aggregate a numeric value pulled from the
        common latency/value keys, falling back to a count when none is present.
        The LQL where-clause and all bounds are parameterized.
        """
        if not self.client:
            return 0.0

        from denoiser.query.parser import compile_to_sql, parse_query

        ast = parse_query(query_string)
        params: dict[str, Any] = {}
        sql_where = compile_to_sql(ast, params)

        if tenant_id:
            sql_where = f"tenant_id = {{tenant_id:String}} AND ({sql_where})"
            params["tenant_id"] = str(tenant_id)
        if from_ts is not None:
            sql_where += " AND timestamp >= toDateTime64({from_ts:Float64}, 3, 'UTC')"
            params["from_ts"] = from_ts / 1000.0
        if to_ts is not None:
            sql_where += " AND timestamp <= toDateTime64({to_ts:Float64}, 3, 'UTC')"
            params["to_ts"] = to_ts / 1000.0

        agg = (aggregation or "count").lower()
        if agg in ("sum", "avg", "max", "min"):
            numeric = (
                "coalesce("
                "nullIf(JSONExtractFloat(raw_json, 'duration_ms'), 0),"
                "nullIf(JSONExtractFloat(raw_json, 'latency'), 0),"
                "nullIf(JSONExtractFloat(raw_json, 'value'), 0), 0)"
            )
            sql = f"SELECT {agg}({numeric}) FROM semantic_logs WHERE {sql_where}"
        else:
            sql = f"SELECT count() FROM semantic_logs WHERE {sql_where}"

        try:
            result = self.client.query(sql, parameters=params)
            value = result.result_rows[0][0] if result.result_rows else 0
            return float(value or 0)
        except Exception as e:
            logger.error(f"Failed to aggregate metric: {e}")
            return 0.0

    def get_facets(self, tenant_id: str = "", from_ts: int | None = None, to_ts: int | None = None):
        """Get facet counts for log explorer sidebar"""
        if not self.client:
            return {"source": [], "level": []}
            
        params = {}
        sql_where = "1=1"
        if tenant_id:
            sql_where += " AND tenant_id = {tenant_id:String}"
            params['tenant_id'] = str(tenant_id)

        if from_ts is not None:
            sql_where += " AND timestamp >= toDateTime64({from_ts:Float64}, 3, 'UTC')"
            params['from_ts'] = from_ts / 1000.0
        if to_ts is not None:
            sql_where += " AND timestamp <= toDateTime64({to_ts:Float64}, 3, 'UTC')"
            params['to_ts'] = to_ts / 1000.0

        facets = {"source": [], "level": []}
        
        try:
            # Source facet
            sql_source = f"SELECT source, count() as count FROM semantic_logs WHERE {sql_where} GROUP BY source ORDER BY count DESC LIMIT 20"
            res_source = self.client.query(sql_source, parameters=params)
            facets["source"] = [{"value": row[0], "count": row[1]} for row in res_source.result_rows]
            
            # Level facet
            sql_level = f"SELECT level, count() as count FROM semantic_logs WHERE {sql_where} GROUP BY level ORDER BY count DESC LIMIT 20"
            res_level = self.client.query(sql_level, parameters=params)
            facets["level"] = [{"value": row[0], "count": row[1]} for row in res_level.result_rows]
            
            return facets
        except Exception as e:
            logger.error(f"Failed to get facets: {e}")
            return {"source": [], "level": []}

    def get_histogram(self, query_string: str = "", tenant_id: str = "", from_ts: int | None = None, to_ts: int | None = None, interval: str = "1 hour"):
        """Get log volume over time for histogram"""
        if not self.client:
            return []
            
        from denoiser.query.parser import compile_to_sql, parse_query
        ast = parse_query(query_string)
        params = {}
        sql_where = compile_to_sql(ast, params)

        if tenant_id:
            sql_where = f"tenant_id = {{tenant_id:String}} AND ({sql_where})"
            params['tenant_id'] = str(tenant_id)

        if from_ts is not None:
            sql_where += " AND timestamp >= toDateTime64({from_ts:Float64}, 3, 'UTC')"
            params['from_ts'] = from_ts / 1000.0
        if to_ts is not None:
            sql_where += " AND timestamp <= toDateTime64({to_ts:Float64}, 3, 'UTC')"
            params['to_ts'] = to_ts / 1000.0

        # Determine grouping interval if not explicitly provided based on time range
        # ClickHouse syntax: toStartOfInterval(timestamp, INTERVAL 1 hour)
        ch_interval = "1 minute"
        if from_ts and to_ts:
            diff_hours = (to_ts - from_ts) / 3600000
            if diff_hours <= 1:
                ch_interval = "1 minute"
            elif diff_hours <= 24:
                ch_interval = "15 minute"
            elif diff_hours <= 24 * 7:
                ch_interval = "1 hour"
            else:
                ch_interval = "1 day"
        else:
            ch_interval = interval

        try:
            sql = f"""
                SELECT 
                    toUnixTimestamp(toStartOfInterval(timestamp, INTERVAL {ch_interval})) * 1000 as time_bucket,
                    count() as count,
                    level
                FROM semantic_logs
                WHERE {sql_where}
                GROUP BY time_bucket, level
                ORDER BY time_bucket ASC
            """
            result = self.client.query(sql, parameters=params)
            
            # Format the output for Recharts (merge levels into single objects per timestamp)
            buckets = {}
            for row in result.result_rows:
                ts = row[0]
                count = row[1]
                level = row[2] or 'INFO'
                
                if ts not in buckets:
                    buckets[ts] = {"timestamp": ts, "count": 0}
                
                buckets[ts]["count"] += count
                # Optional: break down by level if needed
                if level not in buckets[ts]:
                     buckets[ts][level] = 0
                buckets[ts][level] += count
                
            return list(buckets.values())
        except Exception as e:
            logger.error(f"Failed to get histogram: {e}")
            return []
