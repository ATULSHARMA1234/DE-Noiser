from datetime import timedelta

from sqlalchemy.orm import Session

from denoiser.logging import get_logger
from denoiser.storage.clickhouse_store import ClickHouseStore
from denoiser.storage.db import ServiceLevelObjective
from denoiser.utils.time import utcnow

logger = get_logger(__name__)

# Levels that count against an availability SLO.
_BAD_LEVELS = "('error', 'fatal', 'critical')"

# Fields a log line may carry its request duration in, most specific first.
# Everything is normalised to milliseconds; the bare `duration`/`latency` keys
# are assumed to already be milliseconds, which is what every shipper in the
# ingest path emits.
_LATENCY_FIELDS = (
    "duration_ms",
    "latency_ms",
    "elapsed_ms",
    "response_time_ms",
    "duration",
    "latency",
)

# Default objective threshold when an SLO does not carry its own.
DEFAULT_LATENCY_THRESHOLD_MS = 500.0


def _latency_expression() -> str:
    """SQL yielding a log's duration in ms, or NULL when it carries none.

    NULL rather than 0 is the whole point: a log line with no duration has not
    met the objective, it is simply not a measurement, and the two must not be
    conflated.
    """
    branches = "".join(
        f"JSONHas(raw_json, '{field}'), JSONExtractFloat(raw_json, '{field}'), "
        for field in _LATENCY_FIELDS
    )
    return f"multiIf({branches}NULL)"


def _latency_threshold(slo: ServiceLevelObjective) -> float:
    """The SLO's objective in ms, falling back to the platform default."""
    configured = getattr(slo, "latency_threshold_ms", None)
    try:
        value = float(configured)
    except (TypeError, ValueError):
        return DEFAULT_LATENCY_THRESHOLD_MS
    return value if value > 0 else DEFAULT_LATENCY_THRESHOLD_MS


def calculate_slo_status(db: Session, slo: ServiceLevelObjective):
    """
    Calculate the current SLO status by querying real event data from ClickHouse.

    All queries are parameterized: ``slo.service`` is operator-supplied and this
    function now runs on a schedule, so it must not be string-interpolated into
    SQL. Timestamps use the same ``toDateTime64({p:Float64}, 3, 'UTC')`` binding
    the log query path already uses.

    A latency SLI is measured only over the log lines that actually carry a
    duration. Counting duration-less lines as "good" — which is what this did —
    made every latency SLO report 100% forever, because the overwhelming
    majority of log lines have no duration field at all. When nothing in the
    window is measurable the status is ``NO_DATA``, not a passing score.
    """
    end_time = utcnow()
    start_time = end_time - timedelta(days=slo.window_days)

    ch_store = ClickHouseStore()
    client = ch_store.client

    is_latency = slo.sli_type == "latency"

    total_events = 0      # everything in the window
    measured_events = 0   # the denominator of the SLI
    good_events = 0

    # Bound query: source + time window, bound as parameters.
    params = {"service": slo.service, "start": start_time.timestamp()}
    window = "source = {service:String} AND timestamp >= toDateTime64({start:Float64}, 3, 'UTC')"

    if is_latency:
        params["threshold"] = _latency_threshold(slo)
        latency_sql = _latency_expression()

    if client:
        try:
            total_result = client.query(
                f"SELECT count() FROM semantic_logs WHERE {window}",
                parameters=params,
            )
            total_events = total_result.result_rows[0][0] if total_result.result_rows else 0
            measured_events = total_events

            if total_events > 0:
                if slo.sli_type == 'availability':
                    good_result = client.query(
                        f"SELECT count() FROM semantic_logs WHERE {window} AND lower(level) NOT IN {_BAD_LEVELS}",
                        parameters=params,
                    )
                    good_events = good_result.result_rows[0][0] if good_result.result_rows else 0
                elif is_latency:
                    # One pass returns both the measurable population and how
                    # many of those met the objective.
                    good_result = client.query(
                        f"""SELECT
                                countIf({latency_sql} IS NOT NULL) AS measured,
                                countIf({latency_sql} IS NOT NULL
                                        AND {latency_sql} <= {{threshold:Float64}}) AS good
                            FROM semantic_logs WHERE {window}""",
                        parameters=params,
                    )
                    row = good_result.result_rows[0] if good_result.result_rows else (0, 0)
                    measured_events, good_events = int(row[0]), int(row[1])
                else:
                    good_events = total_events
        except Exception as e:
            logger.error(f"Failed to query ClickHouse for SLO calculation: {e}")
            total_events = 0
            measured_events = 0

    # No measurable events means we cannot say anything about this objective.
    # Reporting 100%/HEALTHY here claimed a passing SLO on the strength of no
    # evidence at all, which is the failure mode an SLO exists to prevent.
    if measured_events == 0:
        current_value = 0.0
        error_budget_total = 0
        error_budget_remaining = 0
        burn_rate = 0.0
        status = "NO_DATA"
        data_points = []
    else:
        current_value = good_events / measured_events * 100

        # Error budget math
        allowed_failures_percent = 100.0 - slo.target_percentage
        error_budget_total = int(measured_events * (allowed_failures_percent / 100.0))
        actual_failures = measured_events - good_events
        error_budget_remaining = error_budget_total - actual_failures

        burn_rate = actual_failures / error_budget_total if error_budget_total > 0 else 0.0

        status = "HEALTHY"
        if error_budget_remaining < 0:
            status = "BREACHED"
        elif burn_rate > 0.8:
            status = "WARNING"

        # Generate timeline data points using a real time-series query
        data_points = []
        if client:
            try:
                if is_latency:
                    interval_sql = f"""
                        SELECT
                            toStartOfDay(timestamp) as day,
                            countIf({latency_sql} IS NOT NULL) as total,
                            countIf({latency_sql} IS NOT NULL
                                    AND {latency_sql} <= {{threshold:Float64}}) as good
                        FROM semantic_logs
                        WHERE {window}
                        GROUP BY day
                        ORDER BY day ASC
                    """
                else:
                    interval_sql = f"""
                        SELECT
                            toStartOfDay(timestamp) as day,
                            count() as total,
                            countIf(lower(level) NOT IN {_BAD_LEVELS}) as good
                        FROM semantic_logs
                        WHERE {window}
                        GROUP BY day
                        ORDER BY day ASC
                    """

                ts_result = client.query(interval_sql, parameters=params)
                for row in ts_result.result_rows:
                    day, day_total, day_good = row[0], row[1], row[2]
                    # A day with nothing measurable is a gap in the series, not
                    # a perfect day.
                    if not day_total:
                        continue
                    data_points.append({
                        "timestamp": day.isoformat(),
                        "value": day_good / day_total * 100,
                    })
            except Exception as e:
                logger.error(f"Failed to query timeseries points for SLO: {e}")

    return {
        "slo_id": slo.id,
        "current_value": current_value,
        # Raw event counts so schedulers can persist a real SLI data point rather
        # than re-deriving them from the budget.
        "total_events": total_events,
        # For latency this is smaller than total_events: only the lines that
        # carried a duration are part of the objective.
        "measured_events": measured_events,
        "good_events": good_events,
        "error_budget_total": error_budget_total,
        "error_budget_remaining": error_budget_remaining,
        "burn_rate": burn_rate,
        "status": status,
        "data_points": data_points,
        "threshold_ms": _latency_threshold(slo) if is_latency else None,
    }
