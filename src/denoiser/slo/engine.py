from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from denoiser.logging import get_logger
from denoiser.storage.clickhouse_store import ClickHouseStore
from denoiser.storage.db import ServiceLevelObjective

logger = get_logger(__name__)

# Levels that count against an availability SLO.
_BAD_LEVELS = "('error', 'fatal', 'critical')"


def calculate_slo_status(db: Session, slo: ServiceLevelObjective):
    """
    Calculate the current SLO status by querying real event data from ClickHouse.

    All queries are parameterized: ``slo.service`` is operator-supplied and this
    function now runs on a schedule, so it must not be string-interpolated into
    SQL. Timestamps use the same ``toDateTime64({p:Float64}, 3, 'UTC')`` binding
    the log query path already uses.
    """
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=slo.window_days)

    ch_store = ClickHouseStore()
    client = ch_store.client

    total_events = 0
    good_events = 0

    # Bound query: source + time window, bound as parameters.
    params = {"service": slo.service, "start": start_time.timestamp()}
    window = "source = {service:String} AND timestamp >= toDateTime64({start:Float64}, 3, 'UTC')"

    if client:
        try:
            total_result = client.query(
                f"SELECT count() FROM semantic_logs WHERE {window}",
                parameters=params,
            )
            total_events = total_result.result_rows[0][0] if total_result.result_rows else 0

            if total_events > 0:
                if slo.sli_type == 'availability':
                    good_result = client.query(
                        f"SELECT count() FROM semantic_logs WHERE {window} AND lower(level) NOT IN {_BAD_LEVELS}",
                        parameters=params,
                    )
                    good_events = good_result.result_rows[0][0] if good_result.result_rows else 0
                elif slo.sli_type == 'latency':
                    good_result = client.query(
                        f"""SELECT count() FROM semantic_logs WHERE {window} AND (
                            JSONExtractFloat(raw_json, 'duration_ms') < 500
                            OR JSONExtractFloat(raw_json, 'latency') < 500
                            OR JSONHas(raw_json, 'duration_ms') = 0
                        )""",
                        parameters=params,
                    )
                    good_events = good_result.result_rows[0][0] if good_result.result_rows else 0
                else:
                    good_events = total_events
        except Exception as e:
            logger.error(f"Failed to query ClickHouse for SLO calculation: {e}")
            total_events = 0

    # If ClickHouse failed or returned 0 events, we don't use mock data anymore.
    # We show an empty/perfect state.
    if total_events == 0:
        current_value = 100.0
        error_budget_total = 0
        error_budget_remaining = 0
        burn_rate = 0.0
        status = "HEALTHY"
        data_points = []
    else:
        current_value = (good_events / total_events * 100) if total_events > 0 else 100.0

        # Error budget math
        allowed_failures_percent = 100.0 - slo.target_percentage
        error_budget_total = int(total_events * (allowed_failures_percent / 100.0))
        actual_failures = total_events - good_events
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
                if slo.sli_type == 'latency':
                    interval_sql = f"""
                        SELECT
                            toStartOfDay(timestamp) as day,
                            count() as total,
                            countIf(
                                JSONExtractFloat(raw_json, 'duration_ms') < 500
                                OR JSONExtractFloat(raw_json, 'latency') < 500
                                OR JSONHas(raw_json, 'duration_ms') = 0
                            ) as good
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
                    day_val = (day_good / day_total * 100) if day_total > 0 else 100.0
                    data_points.append({
                        "timestamp": day.isoformat(),
                        "value": day_val
                    })
            except Exception as e:
                logger.error(f"Failed to query timeseries points for SLO: {e}")

    return {
        "slo_id": slo.id,
        "current_value": current_value,
        # Raw event counts so schedulers can persist a real SLI data point rather
        # than re-deriving them from the budget.
        "total_events": total_events,
        "good_events": good_events,
        "error_budget_total": error_budget_total,
        "error_budget_remaining": error_budget_remaining,
        "burn_rate": burn_rate,
        "status": status,
        "data_points": data_points
    }
