from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import random

from denoiser.storage.db import ServiceLevelObjective
from denoiser.storage.clickhouse_store import ClickHouseStore
from denoiser.logging import get_logger

logger = get_logger(__name__)

def calculate_slo_status(db: Session, slo: ServiceLevelObjective):
    """
    Calculate the current SLO status by querying real event data from ClickHouse.
    """
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=slo.window_days)
    
    ch_store = ClickHouseStore()
    client = ch_store.client

    total_events = 0
    good_events = 0

    if client:
        try:
            # Format datetime for ClickHouse
            start_str = start_time.strftime('%Y-%m-%d %H:%M:%S')
            
            # Count total events
            total_query = f"""
                SELECT count() FROM semantic_logs 
                WHERE source = '{slo.service}' 
                AND timestamp >= '{start_str}'
            """
            total_result = client.query(total_query)
            total_events = total_result.result_rows[0][0] if total_result.result_rows else 0
            
            if total_events > 0:
                if slo.sli_type == 'availability':
                    good_query = f"""
                        SELECT count() FROM semantic_logs 
                        WHERE source = '{slo.service}' 
                        AND timestamp >= '{start_str}'
                        AND lower(level) NOT IN ('error', 'fatal', 'critical')
                    """
                    good_result = client.query(good_query)
                    good_events = good_result.result_rows[0][0] if good_result.result_rows else 0
                elif slo.sli_type == 'latency':
                    good_query = f"""
                        SELECT count() FROM semantic_logs 
                        WHERE source = '{slo.service}' 
                        AND timestamp >= '{start_str}'
                        AND (
                            JSONExtractFloat(raw_json, 'duration_ms') < 500
                            OR JSONExtractFloat(raw_json, 'latency') < 500
                            OR JSONHas(raw_json, 'duration_ms') = 0
                        )
                    """
                    good_result = client.query(good_query)
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
        
        if error_budget_total > 0:
            burn_rate = actual_failures / error_budget_total
        else:
            burn_rate = 0.0
            
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
                        WHERE source = '{slo.service}' AND timestamp >= '{start_str}'
                        GROUP BY day
                        ORDER BY day ASC
                    """
                else:
                    interval_sql = f"""
                        SELECT 
                            toStartOfDay(timestamp) as day,
                            count() as total,
                            countIf(lower(level) NOT IN ('error', 'fatal', 'critical')) as good
                        FROM semantic_logs
                        WHERE source = '{slo.service}' AND timestamp >= '{start_str}'
                        GROUP BY day
                        ORDER BY day ASC
                    """
                
                ts_result = client.query(interval_sql)
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
        "error_budget_total": error_budget_total,
        "error_budget_remaining": error_budget_remaining,
        "burn_rate": burn_rate,
        "status": status,
        "data_points": data_points
    }
