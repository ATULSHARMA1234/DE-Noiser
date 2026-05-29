from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
import random

from denoiser.storage.db import ServiceLevelObjective, SLODataPoint, Span

def calculate_slo_status(db: Session, slo: ServiceLevelObjective):
    """
    Calculate the current SLO status based on spans from the database.
    If no spans exist, we'll generate some dummy data for demonstration.
    """
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=slo.window_days)
    
    # In a real system, we'd query Traces/Spans or Metrics to determine SLI
    spans = db.query(Span).filter(
        Span.service_name == slo.service,
        Span.start_time >= start_time
    ).all()
    
    total_events = len(spans)
    
    if total_events > 0:
        if slo.sli_type == 'availability':
            good_events = sum(1 for s in spans if s.status_code != 'ERROR')
        elif slo.sli_type == 'latency':
            # Assuming latency target is 500ms
            good_events = sum(1 for s in spans if s.duration_ms < 500)
        else:
            good_events = total_events
    else:
        # Mock data for sandbox demonstration
        total_events = random.randint(10000, 50000)
        
        # Determine if we should mock a healthy or unhealthy SLO based on target
        if slo.target_percentage >= 99.9:
            # 99.9% target is hard, maybe we have 99.8% actual
            actual_percent = 99.8 + (random.random() * 0.15) 
        else:
            actual_percent = slo.target_percentage + (random.random() * 2)
            
        good_events = int(total_events * (actual_percent / 100.0))
        
    current_value = (good_events / total_events * 100) if total_events > 0 else 100.0
    
    # Error budget math
    allowed_failures_percent = 100.0 - slo.target_percentage
    error_budget_total = int(total_events * (allowed_failures_percent / 100.0))
    actual_failures = total_events - good_events
    error_budget_remaining = error_budget_total - actual_failures
    
    # Burn rate (how fast we are consuming the budget vs expected)
    # If we consumed 50% of budget in 10% of time window -> burn rate = 5
    # For mock data, we just derive it from remaining budget
    burn_rate = 1.0
    if error_budget_remaining < 0:
        burn_rate = random.uniform(2.5, 5.0)
    elif error_budget_remaining < (error_budget_total * 0.2):
        burn_rate = random.uniform(1.1, 2.0)
    else:
        burn_rate = random.uniform(0.1, 0.9)
        
    status = "HEALTHY"
    if error_budget_remaining < 0:
        status = "BREACHED"
    elif burn_rate > 1.5:
        status = "WARNING"
        
    # Generate some timeline data points for chart
    data_points = []
    points_count = min(30, slo.window_days)
    
    for i in range(points_count):
        # random fluctuation around the current value
        point_val = current_value + (random.random() * 0.4 - 0.2)
        point_val = min(100.0, max(0.0, point_val))
        point_time = end_time - timedelta(days=(points_count - i - 1))
        
        data_points.append({
            "timestamp": point_time.isoformat(),
            "value": point_val
        })
        
    return {
        "slo_id": slo.id,
        "current_value": current_value,
        "error_budget_total": error_budget_total,
        "error_budget_remaining": error_budget_remaining,
        "burn_rate": burn_rate,
        "status": status,
        "data_points": data_points
    }
