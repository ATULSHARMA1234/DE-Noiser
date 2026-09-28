"""Monitor evaluation.

A monitor is a stored LQL query plus thresholds. Nothing ran them: the API
could create, mute and delete monitors, and the UI could show whether one was
enabled, but no code ever executed the query — so a monitor never fired, and
"Status" meant nothing more than "not disabled".

This module runs a monitor's query over its window, compares the result against
its thresholds, and records the outcome on the row. Alerting is edge-triggered:
an alert is written when a monitor *enters* a breaching state, not on every
evaluation, so a persistently broken service does not generate one alert per
minute forever.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy.orm import Session

from denoiser.logging import get_logger
from denoiser.storage.db import AlertLog, Monitor
from denoiser.utils.time import to_epoch_ms, utcnow

logger = get_logger(__name__)

# Statuses a monitor can hold. OK/WARNING/CRITICAL come from the thresholds;
# NO_DATA means the query matched nothing and no threshold defines that as a
# breach; ERROR means the query itself could not be run.
STATUS_PENDING = "PENDING"
STATUS_OK = "OK"
STATUS_WARNING = "WARNING"
STATUS_CRITICAL = "CRITICAL"
STATUS_NO_DATA = "NO_DATA"
STATUS_ERROR = "ERROR"

BREACHING_STATUSES = (STATUS_WARNING, STATUS_CRITICAL)

_DEFAULT_WINDOW_SECONDS = 300


@dataclass
class EvaluationResult:
    """One evaluation of one monitor."""
    monitor_id: int
    status: str
    value: float | None
    window_seconds: int
    threshold_warning: float | None
    threshold_critical: float | None
    message: str
    error: str | None = None

    @property
    def is_breaching(self) -> bool:
        return self.status in BREACHING_STATUSES


def _classify(value: float, monitor: Monitor) -> str:
    """Map an observed value onto a status using the monitor's thresholds.

    Thresholds are upper bounds — "alert when at least this many matches" —
    which is what a log-alert monitor means by a threshold. A monitor with no
    thresholds at all alerts on any match, since that is the only sensible
    reading of "tell me when this query matches".
    """
    critical, warning = monitor.threshold_critical, monitor.threshold_warning

    if critical is not None and value >= critical:
        return STATUS_CRITICAL
    if warning is not None and value >= warning:
        return STATUS_WARNING
    if critical is None and warning is None:
        return STATUS_CRITICAL if value > 0 else STATUS_OK
    return STATUS_OK


def evaluate_monitor(monitor: Monitor, store=None, now: datetime.datetime | None = None) -> EvaluationResult:
    """Run one monitor's query and classify the result. Does not write anything."""
    now = now or utcnow()
    window = monitor.window_seconds or _DEFAULT_WINDOW_SECONDS

    if store is None:
        from denoiser import runtime
        store = runtime.clickhouse_store()

    to_ms = to_epoch_ms(now)
    from_ms = to_ms - window * 1000

    try:
        value = float(store.aggregate_metric(
            monitor.query,
            aggregation="count",
            tenant_id=monitor.tenant_id,
            from_ts=from_ms,
            to_ts=to_ms,
        ))
    except Exception as e:
        logger.warning("Monitor %s (%s) failed to evaluate: %s", monitor.id, monitor.name, e)
        return EvaluationResult(
            monitor_id=monitor.id, status=STATUS_ERROR, value=None,
            window_seconds=window, threshold_warning=monitor.threshold_warning,
            threshold_critical=monitor.threshold_critical,
            message=f"Query could not be evaluated: {e}", error=str(e),
        )

    status = _classify(value, monitor)
    if value == 0 and status == STATUS_OK:
        status = STATUS_NO_DATA

    window_label = f"{window // 60}m" if window >= 60 else f"{window}s"
    if status == STATUS_NO_DATA:
        message = f"No matches for `{monitor.query}` in the last {window_label}"
    elif status == STATUS_OK:
        message = f"{value:g} matches in the last {window_label} — within thresholds"
    else:
        threshold = monitor.threshold_critical if status == STATUS_CRITICAL else monitor.threshold_warning
        limit = f" (threshold {threshold:g})" if threshold is not None else ""
        message = f"{value:g} matches for `{monitor.query}` in the last {window_label}{limit}"

    return EvaluationResult(
        monitor_id=monitor.id, status=status, value=value, window_seconds=window,
        threshold_warning=monitor.threshold_warning,
        threshold_critical=monitor.threshold_critical,
        message=monitor.message or message,
    )


def is_muted(monitor: Monitor, now: datetime.datetime | None = None) -> bool:
    now = now or utcnow()
    return bool(monitor.muted_until and monitor.muted_until > now)


def apply_result(db: Session, monitor: Monitor, result: EvaluationResult, now: datetime.datetime | None = None) -> bool:
    """Persist an evaluation and raise an alert on entry into a breaching state.

    Returns whether an alert was written. Edge-triggered on purpose: alerting on
    every evaluation would turn one broken service into an alert per minute.
    Muted monitors are still evaluated — the operator wants the status visible —
    but do not alert.
    """
    now = now or utcnow()
    previous_status = monitor.status or STATUS_PENDING

    monitor.status = result.status
    monitor.last_value = result.value
    monitor.last_evaluated_at = now
    monitor.last_error = result.error

    entering_breach = result.is_breaching and previous_status not in BREACHING_STATUSES
    alerted = False

    if entering_breach:
        monitor.last_triggered_at = now
        if not is_muted(monitor, now):
            db.add(AlertLog(
                webhook_id="monitor_engine",
                alert_fingerprint=f"monitor_{monitor.id}_{result.status}",
                priority="critical" if result.status == STATUS_CRITICAL else "warning",
                status="fired",
                error=f"Monitor '{monitor.name}': {result.message}",
            ))
            alerted = True
            logger.info("Monitor %s (%s) entered %s", monitor.id, monitor.name, result.status)

    return alerted


def evaluate_all(db: Session, store=None, now: datetime.datetime | None = None) -> list[EvaluationResult]:
    """Evaluate every enabled monitor and persist the outcomes."""
    now = now or utcnow()
    monitors = db.query(Monitor).filter(Monitor.enabled).all()
    if not monitors:
        return []

    if store is None:
        from denoiser import runtime
        store = runtime.clickhouse_store()

    results = []
    for monitor in monitors:
        result = evaluate_monitor(monitor, store=store, now=now)
        apply_result(db, monitor, result, now=now)
        results.append(result)

    db.commit()
    return results
