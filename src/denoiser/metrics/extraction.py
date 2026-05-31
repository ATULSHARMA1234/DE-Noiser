from datetime import datetime

from sqlalchemy.orm import Session

from denoiser.logging import get_logger
from denoiser.query.parser import evaluate_in_memory, parse_query
from denoiser.storage.db import ExtractedMetric, MetricRule

logger = get_logger(__name__)

def extract_metrics_from_logs(db: Session, logs: list):
    """
    Evaluate incoming logs against all active MetricRules and extract values.
    For count aggregation, we increment the count.
    """
    rules = db.query(MetricRule).filter(MetricRule.enabled).all()
    if not rules:
        return

    # In a real system, you'd batch these up over a window_seconds period.
    # For simplicity, we just aggregate over the current batch of logs and save a data point.
    now = datetime.utcnow()

    for rule in rules:
        try:
            ast = parse_query(rule.query)
            count = 0

            for log in logs:
                if evaluate_in_memory(ast, log):
                    count += 1

            if count > 0:
                metric = ExtractedMetric(
                    rule_id=rule.id,
                    timestamp=now,
                    value=float(count)
                )
                db.add(metric)
        except Exception as e:
            logger.error(f"Error extracting metric for rule {rule.id}: {e}")

    db.commit()
