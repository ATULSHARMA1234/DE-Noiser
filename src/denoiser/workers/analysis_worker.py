import json
import os
from datetime import UTC, datetime, timedelta

from celery import Celery

from denoiser.analysis import pipeline
from denoiser.logging import get_logger
from denoiser.storage.db import SessionLocal
from denoiser.storage.vector_store import VectorStore

logger = get_logger(__name__)


# Rows per ClickHouse insert when indexing an analysed source.
_INDEX_BATCH_SIZE = 5_000


def index_records_for_search(records: list[dict], tenant_id, run_id: str) -> int:
    """Write the analysed records to the searchable log store.

    Analysis read log files straight off disk and never indexed them, so a
    source you had just analysed returned nothing in Explore, produced no
    extracted metrics, and could not be monitored — every one of those features
    queries ClickHouse, and only the live /ingest path ever wrote to it.

    Returns the number of rows indexed (0 when the store is unavailable, which
    is not fatal: the analysis result itself does not depend on it).
    """
    if not tenant_id or not records:
        return 0

    try:
        from denoiser import runtime

        store = runtime.clickhouse_store()
        if not store.client:
            logger.warning("ClickHouse unavailable; analysed logs were not indexed for search")
            return 0

        # `raw_text` arrives already redacted from the read loop, so the copy
        # made searchable here carries no secrets. Indexing it verbatim was what
        # made every password and card number in an analysed file queryable
        # through /v1/logs/query.
        indexed = 0
        for start in range(0, len(records), _INDEX_BATCH_SIZE):
            batch = []
            for record in records[start:start + _INDEX_BATCH_SIZE]:
                metadata = {}
                if record.get("metadata"):
                    try:
                        metadata = json.loads(record["metadata"])
                    except (TypeError, ValueError):
                        metadata = {}

                entry = {
                    "message": record["raw_text"],
                    # resolve_source/resolve_level read these keys, so a log that
                    # names its own service or level keeps it; the file it came
                    # from is the fallback identity.
                    "service": metadata.get("service") or record.get("source_label"),
                    "source": record.get("source_label"),
                    "run_id": run_id,
                    **{k: v for k, v in metadata.items() if k not in ("service",)},
                }
                if record.get("timestamp") is not None:
                    entry["timestamp"] = record["timestamp"].isoformat()
                batch.append(entry)

            if store.insert_logs(batch, tenant_id=str(tenant_id)):
                indexed += len(batch)

        if indexed:
            logger.info("Indexed %d analysed log lines for search (run %s)", indexed, run_id)
        return indexed
    except Exception as e:
        logger.warning(f"Failed to index analysed logs for search: {e}")
        return 0








# Initialize Celery using local Redis if URL not provided
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
is_testing = "PYTEST_CURRENT_TEST" in os.environ

if is_testing:
    celery_app = Celery('analysis_worker', broker='memory://', backend='cache+memory://')
    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
    )
else:
    celery_app = Celery('analysis_worker', broker=redis_url, backend=redis_url)
    celery_app.conf.update(task_track_started=True, result_expires=86400)

# Setup LanceDB
vector_store = VectorStore()

@celery_app.task(bind=True)
def run_analysis_task(self, request_dict: dict):
    """Run one analysis and record it.

    The 461-line body this replaced is now `denoiser.analysis.pipeline`, staged
    over one explicit run state. What is left here is what is genuinely Celery's:
    unpacking the message, reporting progress, and owning the database session.
    """
    logger.info(f"Starting async analysis task: {self.request.id}")

    request = pipeline.RunRequest.from_dict(
        request_dict, run_id=self.request.id or ""
    )

    def progress(percent: int, status: str) -> None:
        self.update_state(state="PROGRESS", meta={"progress": percent, "status": status})

    try:
        state = pipeline.analyse(request, vector_store=vector_store, progress=progress)
    except pipeline.RunAborted as e:
        return {"status": "error", "message": str(e)}

    progress(95, "Saving to Database")
    db = SessionLocal()
    try:
        incident = pipeline.persist(state, db, index=index_records_for_search)
        pipeline.announce(
            state, db, alert=pipeline.pending_alert(state), incident=incident
        )
    except Exception as e:
        logger.error(f"DB Error: {e}")
        db.rollback()
        state.failed("persistence", e)
    finally:
        db.close()

    return pipeline.result(state)


@celery_app.task
def evaluate_slos():
    """
    Periodic task to evaluate all active Service Level Objectives (SLOs)
    and calculate the error budget / burn rate.
    """
    logger.info("Evaluating active Service Level Objectives (SLOs)...")
    db = SessionLocal()
    try:
        from denoiser.slo.engine import calculate_slo_status
        from denoiser.storage.db import ServiceLevelObjective, SLODataPoint

        slos = db.query(ServiceLevelObjective).all()

        for slo in slos:
            try:
                # Real SLI from ingested logs — no fabricated data. When nothing
                # in the window could be measured the engine reports NO_DATA and
                # we record nothing rather than inventing a data point. For a
                # latency SLO the denominator is the measurable subset, not every
                # log line, so persist that as the data point's total.
                status = calculate_slo_status(db, slo)
                measured_events = status.get("measured_events", status.get("total_events", 0))
                good_events = status.get("good_events", 0)
                value = status.get("current_value", 0.0)

                if status.get("status") == "NO_DATA" or measured_events <= 0:
                    # Nothing measurable this cycle; skip rather than record a
                    # perfect score earned by an absence of evidence.
                    continue
                total_events = measured_events

                dp = SLODataPoint(
                    slo_id=slo.id,
                    timestamp=datetime.now(UTC),
                    good_events=good_events,
                    total_events=total_events,
                    value=value
                )
                db.add(dp)

                if value < slo.target_percentage:
                    # Current SLO Breach -> Incident.
                    # Dedup: one OPEN breach incident per SLO. If an incident is
                    # already open for this breach, refresh it instead of opening
                    # a duplicate every evaluation cycle (was ~5/min of spam).
                    from denoiser.storage.db import Incident
                    existing = db.query(Incident).filter(
                        Incident.tenant_id == slo.tenant_id,
                        Incident.domain == slo.service,
                        Incident.title == f"SLO Breach: {slo.name}",
                        Incident.status == "OPEN",
                    ).order_by(Incident.created_at.desc()).first()

                    if existing:
                        # Already an open breach for this SLO — refresh the live
                        # actual value in place. Do NOT touch created_at: that marks
                        # when the incident first opened, not when it was last seen.
                        existing.summary = (
                            f"SLO '{slo.name}' breached for service '{slo.service}'. "
                            f"Target: {slo.target_percentage}%, Actual: {value:.2f}%"
                        )
                        db.commit()
                    else:
                        incident = Incident(
                            tenant_id=slo.tenant_id,
                            title=f"SLO Breach: {slo.name}",
                            domain=slo.service,
                            severity="P1",
                            impact_score=1.0,
                            summary=f"SLO '{slo.name}' breached for service '{slo.service}'. Target: {slo.target_percentage}%, Actual: {value:.2f}%",
                            remediation_hints=["Check recent deployments", "Scale up service replicas"],
                            source="SLO Evaluator",
                            is_predictive=False
                        )
                        db.add(incident)
                        db.commit()  # commit early to trigger runbook
                        db.refresh(incident)

                        try:
                            from denoiser.automation.engine import process_incident
                            process_incident(db, incident)
                        except Exception as auto_err:
                            logger.error(f"Failed to execute runbook on SLO breach: {auto_err}")

                else:
                    # Evaluate Predictive AI / Anomaly Forecasting using Holt-Winters
                    # Fetch last 30 data points for better forecasting
                    recent_points = db.query(SLODataPoint).filter(SLODataPoint.slo_id == slo.id).order_by(SLODataPoint.timestamp.desc()).limit(30).all()
                    if len(recent_points) >= 10:
                        recent_points.reverse() # chronological

                        from statsmodels.tsa.holtwinters import ExponentialSmoothing

                        y_values = [p.value for p in recent_points]

                        try:
                            # Fit Holt-Winters model (no seasonality since we have limited data points, just trend)
                            model = ExponentialSmoothing(y_values, trend="add", initialization_method="estimated")
                            fit_model = model.fit()

                            # Forecast next 240 minutes (assuming 1 datapoint = 1 minute based on cron)
                            forecast = fit_model.forecast(240)

                            # Find when forecast dips below target
                            minutes_to_depletion = -1
                            for i, f_val in enumerate(forecast):
                                if f_val < slo.target_percentage:
                                    minutes_to_depletion = i + 1
                                    break

                            if minutes_to_depletion > 0: # Depletes within 4 hours
                                forecasted_time = datetime.now(UTC) + timedelta(minutes=minutes_to_depletion)
                                from denoiser.storage.db import Incident

                                # Check if we already have a predictive incident for this SLO recently to avoid spam
                                recent_predictive = db.query(Incident).filter(
                                    Incident.tenant_id == slo.tenant_id,
                                    Incident.title == f"Predictive Warning: {slo.name} will breach soon",
                                    Incident.status == "OPEN"
                                ).first()

                                if not recent_predictive:
                                    incident = Incident(
                                        tenant_id=slo.tenant_id,
                                        title=f"Predictive Warning: {slo.name} will breach soon",
                                        domain=slo.service,
                                        severity="P2",
                                        impact_score=0.8,
                                        summary=f"Predictive AI (Holt-Winters) foresees SLO '{slo.name}' for service '{slo.service}' will breach its target of {slo.target_percentage}% in approx {int(minutes_to_depletion)} minutes.",
                                        remediation_hints=["Investigate current trend", "Rollback recent deployment"],
                                        source="SLO Evaluator",
                                        is_predictive=True,
                                        forecasted_depletion_time=forecasted_time
                                    )
                                    db.add(incident)
                                    db.commit()
                                    db.refresh(incident)

                                    try:
                                        from denoiser.automation.engine import process_incident
                                        process_incident(db, incident)
                                    except Exception as auto_err:
                                        logger.error(f"Failed to execute runbook on predictive SLO breach: {auto_err}")
                        except Exception as e:
                            logger.error(f"Holt-Winters forecasting failed for SLO {slo.id}: {e}")

            except Exception as e:
                logger.error(f"Failed to evaluate SLO {slo.id}: {e}")

        db.commit()
        logger.info(f"Evaluated {len(slos)} SLOs.")
    except Exception as e:
        logger.error(f"SLO evaluation failed: {e}")
        db.rollback()
    finally:
        db.close()

@celery_app.task
def extract_metrics():
    """
    Periodic task to evaluate MetricRules and store results in ExtractedMetric.
    """
    logger.info("Extracting metrics from logs...")
    db = SessionLocal()
    try:
        from denoiser import runtime
        from denoiser.storage.db import ExtractedMetric, MetricRule
        from denoiser.storage.errors import StoreUnavailable

        rules = db.query(MetricRule).filter(MetricRule.enabled).all()
        ch_store = runtime.clickhouse_store()

        now = datetime.now(UTC)
        for rule in rules:
            try:
                # Aggregate the rule's real matches over its own window_seconds,
                # in ClickHouse — no synthetic multipliers.
                window_ms = int((rule.window_seconds or 60) * 1000)
                to_ms = int(now.timestamp() * 1000)
                from_ms = to_ms - window_ms

                value = ch_store.aggregate_metric(
                    rule.query,
                    aggregation=rule.aggregation or "count",
                    tenant_id=rule.tenant_id,
                    from_ts=from_ms,
                    to_ts=to_ms,
                )

                dp = ExtractedMetric(
                    rule_id=rule.id,
                    tenant_id=rule.tenant_id,
                    timestamp=now,
                    value=value
                )
                db.add(dp)
            except StoreUnavailable as e:
                # A gap in the series, not a zero in it. Recording the store's
                # own absence as a measurement would put a permanent flat line
                # through every outage, and nothing downstream could ever tell
                # it apart from a genuinely quiet window.
                logger.warning(
                    "Skipped metric rule %s: %s", rule.id, e,
                )
            except Exception as e:
                logger.error(f"Failed to extract metric for rule {rule.id}: {e}")

        db.commit()
    except Exception as e:
        logger.error(f"Metric extraction failed: {e}")
        db.rollback()
    finally:
        db.close()

@celery_app.task
def evaluate_monitors():
    """Periodic task: run every enabled monitor's query and fire on breach."""
    from denoiser.monitors.evaluator import evaluate_all

    db = SessionLocal()
    try:
        results = evaluate_all(db)
        breaching = [r for r in results if r.is_breaching]
        logger.info(
            "Evaluated %d monitors (%d breaching)", len(results), len(breaching)
        )
        return {"evaluated": len(results), "breaching": len(breaching)}
    except Exception as e:
        logger.error(f"Monitor evaluation failed: {e}")
        db.rollback()
        return {"evaluated": 0, "error": str(e)}
    finally:
        db.close()


@celery_app.task
def aggregate_billing():
    """Periodic task: meter per-tenant usage and enforce tier retention.

    This lived on a second Celery app with its own beat that no deployment ever
    started, so usage was never metered and retention was never applied. It runs
    on the platform's own beat now.
    """
    from denoiser.workers.billing_worker import aggregate_billing as run_aggregation

    try:
        return run_aggregation()
    except Exception as e:
        logger.error(f"Billing aggregation failed: {e}")
        return {"error": str(e)}


# Setup periodic tasks
@celery_app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    from celery.schedules import crontab

    # Execute every minute
    sender.add_periodic_task(60.0, evaluate_slos.s(), name='evaluate_slos_every_minute')
    sender.add_periodic_task(60.0, extract_metrics.s(), name='extract_metrics_every_minute')
    sender.add_periodic_task(60.0, evaluate_monitors.s(), name='evaluate_monitors_every_minute')
    # Usage metering + tier retention, daily at midnight UTC.
    sender.add_periodic_task(
        crontab(minute=0, hour=0), aggregate_billing.s(), name='aggregate_billing_daily'
    )
