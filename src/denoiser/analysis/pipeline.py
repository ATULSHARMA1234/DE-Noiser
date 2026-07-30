"""One analysis run, as stages over one explicit state.

`run_analysis_task` was a single 461-line function. Its stage boundaries were
numbered comments (`# 6. Clustering`), which meant three things:

* **State was threaded by hand.** The persistence block read about fifteen live
  locals, some created three hundred lines earlier. The code had already been
  bitten by it twice — once by a `tenant_id` re-read from the request that
  shadowed the resolved one, and once by two values bound inside `if llm_payload:`
  and read seventy lines later under a *second* `if llm_payload:`, correct only
  for as long as those two conditions stayed identical.

* **Partial failure was invisible.** Twelve `except` clauses, nine of them
  log-and-continue, so a run could return `"status": "success"` with clustering
  done but severity, causal links, metrics context, issue tracking, alerting and
  runbooks all silently skipped. The caller could not tell.

* **Nothing was testable without a broker.** The only entry point was a Celery
  task, so the ingest→cluster half of the pipeline had no test that ran it as a
  path — the very thing that made restructuring it feel unsafe.

Each stage here takes the state and returns it. A stage that fails records a
`StageFailure` on the state and the run continues; `RunAborted` is for the cases
where continuing is meaningless (nothing readable, nothing to cluster). The
result reports both, so "success" now means "every stage ran" and anything less
says which one did not.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from denoiser.logging import get_logger
from denoiser.storage.runs import format_clusters

logger = get_logger(__name__)

#: Default cap on raw lines pulled into memory for one run. Without a cap, a
#: multi-million-line source loads entirely into a list → polars frame → per-row
#: objects, which can OOM the worker.
DEFAULT_MAX_ANALYSIS_LINES = 500_000


class RunAborted(Exception):
    """The run cannot produce a result. Carries the message the caller returns."""


@dataclass(frozen=True)
class StageFailure:
    """A stage that did not run, named, so the result can say so."""

    stage: str
    error: str


@dataclass
class RunRequest:
    """What was asked for. Parsed once, at the edge."""

    sources: list[str]
    tenant_id: Any = None
    baseline: str | None = None
    intelligence: bool = False
    top_n: int = 3
    max_lines: int = DEFAULT_MAX_ANALYSIS_LINES
    run_id: str = ""

    @classmethod
    def from_dict(cls, request_dict: dict, *, run_id: str = "") -> RunRequest:
        sources = request_dict.get("sources") or []
        if not sources and request_dict.get("source"):
            sources = [request_dict["source"]]
        return cls(
            sources=[str(s) for s in sources if s],
            tenant_id=request_dict.get("tenant_id"),
            baseline=request_dict.get("baseline"),
            intelligence=bool(request_dict.get("intelligence", False)),
            top_n=request_dict.get("top_n", 3),
            max_lines=resolve_max_lines(request_dict),
            run_id=run_id or f"run_{uuid.uuid4().hex[:8]}",
        )

    @property
    def source_name(self) -> str:
        return ", ".join(self.sources)


def resolve_max_lines(request_dict: dict) -> int:
    """Effective per-run line cap: request `max_lines`, else env, else default."""
    import os

    return int(
        request_dict.get("max_lines")
        or os.getenv("SEMANTICOS_MAX_ANALYSIS_LINES", str(DEFAULT_MAX_ANALYSIS_LINES))
    )


@dataclass
class RunState:
    """Everything a run has produced so far.

    One object rather than fifteen locals: a stage cannot read a value that an
    earlier branch happened not to assign, because every field has a declared
    default and the stage that fills it says so.
    """

    request: RunRequest
    started_at: float = field(default_factory=time.time)

    records: list[dict] = field(default_factory=list)
    truncated: bool = False
    deduper: Any = None
    unique_templates: list[str] = field(default_factory=list)
    vectors: Any = None
    clusters: list[Any] = field(default_factory=list)
    anomalies: dict | None = None
    llm_payload: dict | None = None
    formatted_clusters: list[dict] = field(default_factory=list)
    causal_links: list[Any] = field(default_factory=list)
    formatted_links: list[dict] = field(default_factory=list)
    metrics_context: dict = field(
        default_factory=lambda: {
            "status": "disabled", "clusters_correlated": 0, "clusters_total": 0
        }
    )
    failures: list[StageFailure] = field(default_factory=list)

    @property
    def total_logs(self) -> int:
        return self.deduper.total_count if self.deduper is not None else 0

    @property
    def duration_sec(self) -> float:
        return time.time() - self.started_at

    def failed(self, stage: str, error: Exception | str) -> None:
        """Record a stage that did not run. The run continues without it."""
        message = str(error)
        self.failures.append(StageFailure(stage=stage, error=message))
        logger.error("Analysis stage %r failed for run %s: %s",
                     stage, self.request.run_id, message)


#: Called with (percent, human status) as the run progresses.
Progress = Callable[[int, str], None]


def _noop_progress(percent: int, status: str) -> None:
    pass


# ── Stages ───────────────────────────────────────────────────────────────────

def ingest(state: RunState) -> RunState:
    """Resolve the requested sources, read them, redact as we go.

    Redaction happens at the point of reading, before anything is retained, so
    every downstream consumer inherits redacted text: the cluster
    `representative_raw` persisted into the snapshot and returned by the API, the
    copy indexed into ClickHouse for search, and the sample lines handed to the
    LLM. Redacting only the embedding input left all three carrying the
    original secrets.
    """
    from denoiser.api.platform_settings import build_redactor
    from denoiser.api.sources import SourceNotAllowed, resolve_source
    from denoiser.ingestion.reader import LogReader
    from denoiser.preprocessing.timestamp import TimestampExtractor

    request = state.request

    # Resolve every requested source against the caller's tenant before opening
    # anything. `source` arrives as a free-form string from the API, so without
    # this the reader would happily open /etc/passwd or the deployment's .env
    # and return their contents in the run results.
    resolved: list[tuple[str, Path]] = []
    rejected: list[str] = []
    for src in request.sources:
        try:
            resolved.append((src, resolve_source(src, request.tenant_id)))
        except SourceNotAllowed as e:
            rejected.append(str(e))
            logger.warning("Rejected analysis source", extra={"source": src[:200]})

    if not resolved:
        raise RunAborted(rejected[0] if rejected else "No readable source(s) provided")

    reader = LogReader()
    timestamps = TimestampExtractor()
    redactor = build_redactor()

    for original, src_path in resolved:
        if state.truncated:
            break
        src = str(src_path)
        source_label = Path(original).stem
        try:
            for record in reader.read(src):
                # Timestamp extraction runs on the original line: redaction can
                # rewrite digit runs, and a masked timestamp is unparseable.
                epoch_ms = timestamps.extract(record.raw_text)
                dt = (
                    datetime.fromtimestamp(epoch_ms / 1000.0, UTC)
                    if epoch_ms is not None
                    else None
                )

                state.records.append({
                    "raw_text": redactor.redact(record.raw_text),
                    "source_path": record.source,
                    "source_label": source_label,
                    "line_number": record.line_number,
                    "timestamp": dt,
                    "timestamp_ms": epoch_ms or 0,
                    "metadata": json.dumps(record.metadata),
                })

                if len(state.records) >= request.max_lines:
                    state.truncated = True
                    logger.warning(
                        f"Analysis input capped at max_lines={request.max_lines}; "
                        f"remaining lines in {src} (and any later sources) were "
                        f"skipped for this run."
                    )
                    break
        except Exception as e:
            logger.warning(f"Failed to read source {src}: {e}")

    if not state.records:
        raise RunAborted("No logs found at source(s)")

    return state


def normalize_and_dedupe(state: RunState) -> RunState:
    """Template the redacted lines and collapse them to unique templates.

    `raw_text` is already redacted by `ingest`, so templating works from the
    masked form and no second pass is needed.
    """
    import polars as pl

    from denoiser.ingestion.models import LogRecord
    from denoiser.preprocessing.deduplication import Deduplicator
    from denoiser.preprocessing.normalization import Normalizer

    df = pl.DataFrame(state.records)
    normalized = Normalizer().normalize_batch(df["raw_text"].to_list())
    df = df.with_columns(pl.Series("normalized_text", normalized))

    deduper = Deduplicator()
    for row in df.iter_rows(named=True):
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        meta["source_label"] = row["source_label"]
        deduper.add(LogRecord(
            raw_text=row["raw_text"],
            source=row["source_path"],
            line_number=row["line_number"],
            timestamp=row["timestamp"],
            metadata=meta,
            normalized_text=row["normalized_text"],
        ))

    state.deduper = deduper
    state.unique_templates = deduper.get_unique_templates()
    if not state.unique_templates:
        raise RunAborted("No unique log templates found")

    return state


def embed(state: RunState, vector_store: Any = None) -> RunState:
    """Embed the unique templates, and index them for semantic search.

    Persisting to the vector store is best-effort — the clustering below needs
    the vectors, not the index — so a LanceDB failure is recorded and the run
    continues rather than being swallowed the way it was.
    """
    from denoiser.embeddings.provider import LocalEmbeddingProvider

    state.vectors = LocalEmbeddingProvider().embed(state.unique_templates)

    if vector_store is None:
        return state

    try:
        groups = state.deduper.get_all_groups()
        sources: list[str] = []
        timestamps: list[int] = []
        for template in state.unique_templates:
            records = groups.get(template, [])
            first = records[0] if records else None
            sources.append(
                first.metadata.get("source_label", first.source) if first else "unknown"
            )
            timestamps.append(
                int(first.timestamp.timestamp() * 1000)
                if first and first.timestamp
                else int(time.time() * 1000)
            )

        vector_store.add_embeddings(
            ids=[str(uuid.uuid4()) for _ in state.unique_templates],
            vectors=state.vectors,
            templates=state.unique_templates,
            sources=sources,
            timestamps=timestamps,
            # Log templates carry table names, endpoints and internal hostnames,
            # so they are stamped with their owner like every other store.
            tenant_id=state.request.tenant_id,
        )
    except Exception as e:
        state.failed("vector_index", e)

    return state


def cluster(state: RunState) -> RunState:
    from denoiser.clustering.hdbscan_clusterer import LogClusterer

    state.clusters = LogClusterer().fit_predict(
        state.unique_templates,
        state.vectors,
        state.deduper.get_all_groups(),
        state.deduper.get_all_counts(),
    )
    return state


def score_anomalies(state: RunState) -> RunState:
    """Score templates against a baseline, when the run named one."""
    if not state.request.baseline:
        return state

    from denoiser.baselines.manager import BaselineManager
    from denoiser.detection.scorer import AnomalyScorer

    try:
        manager = BaselineManager(state.request.baseline)
        results = AnomalyScorer(manager).score_batch(
            state.unique_templates, state.vectors
        )
        state.anomalies = {res.template: res for res in results}
    except Exception as e:
        # Previously uncaught, so a missing or corrupt baseline file failed the
        # whole run after the expensive stages had already been paid for.
        state.failed("anomaly_scoring", e)

    return state


def summarise(state: RunState) -> RunState:
    """Ask the LLM for an incident narrative, when the run asked for one."""
    if not state.request.intelligence:
        return state

    from denoiser.intelligence.llm import IncidentIntelligence

    try:
        # Enabled for this run only. The task used to assign
        # `settings.llm_enabled = True` — a process-wide singleton — so the
        # first run that asked for intelligence turned it on for every run
        # after it in the same worker.
        state.llm_payload = IncidentIntelligence(enabled=True).generate_summary(
            state.clusters, state.anomalies, top_n=state.request.top_n
        )
    except Exception as e:
        state.failed("intelligence", e)

    return state


def format_response(state: RunState) -> RunState:
    state.formatted_clusters = format_clusters(
        state.clusters,
        state.anomalies,
        (state.llm_payload or {}).get("cluster_summaries", []),
    )
    return state


def correlate(state: RunState) -> RunState:
    """Cross-service causal links, severity labels, and metrics context.

    Three independent enrichments, each of which may fail on its own without
    invalidating the clustering. They are named separately in `state.failures`
    so a result that is missing severity says "severity", not "success".
    """
    _causal_links(state)
    _severity(state)
    _metrics_context(state)
    return state


def _causal_links(state: RunState) -> None:
    from denoiser.detection.causal_scorer import CausalScorer

    try:
        state.causal_links = CausalScorer().analyze(
            state.clusters, state.deduper.get_all_groups()
        )
    except Exception as e:
        state.failed("causal_links", e)
        return

    narratives: dict[str, str] = {}
    if state.request.intelligence and state.causal_links:
        from denoiser.intelligence.llm import IncidentIntelligence

        try:
            narratives = IncidentIntelligence(enabled=True).narrate_causal_links(
                state.causal_links
            )
        except Exception as e:
            state.failed("causal_narration", e)

    for link in state.causal_links:
        key = f"{link.source_service}->{link.target_service}"
        state.formatted_links.append({
            "source_cluster_id": link.source_cluster_id,
            "target_cluster_id": link.target_cluster_id,
            "source_service": link.source_service,
            "target_service": link.target_service,
            "source_template": link.source_template,
            "target_template": link.target_template,
            "confidence": link.confidence,
            "avg_delay_ms": link.avg_delay_ms,
            "occurrences": link.occurrences,
            "direction": link.direction,
            "narrative": narratives.get(key) or (
                f"Anomalous pattern in {link.source_service} co-occurred with a "
                f"warning in {link.target_service} after an average delay of "
                f"{link.avg_delay_ms:.1f}ms "
                f"(Confidence: {link.confidence * 100:.0f}%)."
            ),
        })


def _severity(state: RunState) -> None:
    from denoiser.detection.severity import SeverityScorer

    try:
        severities = SeverityScorer().score_all(
            state.clusters, state.anomalies, state.causal_links
        )
    except Exception as e:
        state.failed("severity", e)
        return

    for cluster_data in state.formatted_clusters:
        sev = severities.get(cluster_data["cluster_id"])
        cluster_data["priority"] = sev.priority if sev else "P3"
        cluster_data["composite_severity_score"] = sev.composite_score if sev else 0.0
        cluster_data["severity_breakdown"] = sev.breakdown if sev else {}
        cluster_data["keyword_flag"] = sev.keyword_flag if sev else False


def _metrics_context(state: RunState) -> None:
    from denoiser.detection.metrics_correlator import MetricsCorrelator

    try:
        correlator = MetricsCorrelator()
        total = 0
        correlated = 0
        for cluster_data in state.formatted_clusters:
            if cluster_data.get("cluster_id") == -1:
                cluster_data["metrics_context"] = {"status": "skipped_noise"}
                continue
            total += 1
            if cluster_data.get("priority", "P3") not in ("P0", "P1", "P2"):
                cluster_data["metrics_context"] = {"status": "skipped_non_incident"}
                continue
            ts_ms = int(cluster_data.get("representative_timestamp_ms") or 0)
            if ts_ms <= 0:
                cluster_data["metrics_context"] = {"status": "no_timestamp"}
                continue
            ctx = correlator.get_context_for_anomaly(ts_ms, window_ms=30000)
            cluster_data["metrics_context"] = ctx
            if ctx.get("status") == "correlated":
                correlated += 1
        state.metrics_context = {
            "status": "correlated" if correlated > 0 else "no_data",
            "clusters_correlated": correlated,
            "clusters_total": total,
        }
    except Exception as e:
        state.failed("metrics_context", e)
        state.metrics_context = {"status": "error", "message": str(e)}


# ── The pipeline ─────────────────────────────────────────────────────────────

def analyse(
    request: RunRequest,
    *,
    vector_store: Any = None,
    progress: Progress = _noop_progress,
) -> RunState:
    """Run every stage that produces a result, in order.

    Persistence and announcement are deliberately not here: they need a database
    session and perform outbound I/O, and keeping them out is what lets the whole
    analytical half be exercised without either.
    """
    state = RunState(request=request)

    progress(10, "Ingesting files")
    ingest(state)

    progress(30, "Redacting and Normalizing")
    normalize_and_dedupe(state)

    progress(50, "Generating Embeddings")
    embed(state, vector_store=vector_store)

    progress(60, "Clustering Logs")
    cluster(state)
    score_anomalies(state)

    if request.intelligence:
        progress(80, "Generating AI Summary")
    summarise(state)

    format_response(state)
    correlate(state)

    return state


# ── Persistence and announcement ─────────────────────────────────────────────

def persist(state: RunState, db: Any, *, index: Callable[..., Any] | None = None) -> Any:
    """Write the run, its issues and its incident. Commits. Returns the incident.

    `index` writes the analysed lines to the searchable log store; it is a
    parameter so this can be tested without one.
    """
    from denoiser.storage.db import Incident
    from denoiser.storage.runs import record_run, worst_priority

    request = state.request

    if index is not None:
        try:
            index(state.records, request.tenant_id, request.run_id)
        except Exception as e:
            state.failed("search_index", e)

    record_run(
        db,
        run_id=request.run_id,
        tenant_id=request.tenant_id,
        source=request.source_name,
        raw_lines=state.total_logs,
        clusters_snapshot=state.formatted_clusters,
        duration_sec=state.duration_sec,
    )

    # Fold this run's clusters into the tenant's durable issues, so the same
    # pattern keeps one identity (and its triage state) across runs.
    try:
        from denoiser.analysis.issues import upsert_issues

        upsert_issues(
            db, request.tenant_id, request.run_id, state.formatted_clusters,
            clusters=state.clusters, groups=state.deduper.get_all_groups(),
        )
    except Exception as e:
        state.failed("issue_tracking", e)

    incident = None
    if state.llm_payload:
        incident = Incident(
            tenant_id=request.tenant_id,
            title=state.llm_payload.get("failure_domain", "Unknown Failure"),
            domain=state.llm_payload.get("failure_domain", "System"),
            severity=worst_priority(state.formatted_clusters),
            impact_score=(
                min(1.0, len(state.clusters) / 10.0) if len(state.clusters) > 1 else 0.3
            ),
            summary=state.llm_payload.get("incident_summary", ""),
            remediation_hints=state.llm_payload.get("root_cause_hints", []),
            run_id=request.run_id,
            source=request.source_name,
            total_logs=state.total_logs,
            cluster_count=len(state.clusters),
        )
        db.add(incident)

    db.commit()
    return incident


def pending_alert(state: RunState) -> Any:
    """The alert this run would raise, or None. Pure — assembles, sends nothing.

    Built from the state rather than from locals bound inside an `if llm_payload:`
    branch and read again seventy lines later under a second one.
    """
    from denoiser.integrations.alert_router import AlertPayload
    from denoiser.storage.runs import worst_priority

    if not state.llm_payload:
        return None

    # A rank-based min correctly surfaces P2 as the worst when no P0/P1 is
    # present — the loop this replaced only ever promoted to P0/P1, so a
    # top-severity of P2 stayed P3 and never reached the gate below.
    priority = worst_priority(state.formatted_clusters)
    if priority not in ("P0", "P1", "P2"):
        return None

    first = state.formatted_clusters[0] if state.formatted_clusters else {}
    return AlertPayload(
        source=state.request.source_name,
        run_id=state.request.run_id,
        priority=priority,
        cluster_id=first.get("cluster_id", 0),
        cluster_summary=state.llm_payload.get("incident_summary", "Anomaly Detected"),
        representative_log=first.get("representative_log", "") or "No log available",
        anomaly_score=first.get("anomaly_score", 0.0),
        causal_links=state.formatted_links,
        intelligence=state.llm_payload,
        keyword_flag=first.get("keyword_flag", False),
    )


def announce(state: RunState, db: Any, *, alert: Any, incident: Any) -> None:
    """Tell people. Runs only after `persist` has committed.

    Every line of this performs I/O someone else controls: an SMTP handshake,
    webhook delivery with retries and a ten-second timeout per attempt, and
    whatever a runbook's steps do. It used to run *inside* the open transaction,
    so one unresponsive Slack endpoint held a Postgres transaction — and its
    pooled connection — open for tens of seconds; with pool_size=20, enough
    concurrent analyses exhaust the pool and it surfaces as unrelated API
    requests hanging, which points investigation at the database.

    It is also the right order on its own terms: an alert that arrives before
    the data it refers to is committed can be acted on before it can be read.
    """
    import asyncio

    from denoiser.integrations.alert_router import alert_router
    from denoiser.integrations.email import email_notifier

    if alert is not None:
        if alert.priority in ("P0", "P1"):
            try:
                email_notifier.send_alert(alert)
            except Exception as e:
                state.failed("email_alert", e)

        try:
            # Only this tenant's destinations. Dispatching against a global
            # registry would deliver one customer's log content to another
            # customer's Slack channel.
            from denoiser.integrations import webhook_store

            destinations = (
                webhook_store.destinations_for_tenant(db, state.request.tenant_id)
                if state.request.tenant_id is not None else []
            )
            if destinations:
                # AlertRouter persists each delivery record itself, with the
                # owning tenant attached, so there is no second write here.
                asyncio.run(alert_router.dispatch(alert, destinations=destinations))
        except Exception as e:
            state.failed("webhook_dispatch", e)

    if incident is not None:
        try:
            from denoiser.automation.engine import process_incident

            process_incident(db, incident)
        except Exception as e:
            state.failed("runbooks", e)


def result(state: RunState) -> dict:
    """The task's return value.

    `status` stays "success" — the run did produce a result, and the web client
    and the runs API both key off that string. What is new is `complete` and
    `skipped_stages`: a run that clustered but lost severity, causal links and
    metrics context used to report success with no indication at all that three
    quarters of its enrichment was missing. Now it says which stages it lost.
    """
    return {
        "status": "success",
        "complete": not state.failures,
        "run_id": state.request.run_id,
        "clusters": state.formatted_clusters,
        "intelligence": state.llm_payload,
        "causal_links": state.formatted_links,
        "metrics_context": state.metrics_context,
        "total_logs": state.total_logs,
        "total_logs_analyzed": state.total_logs,
        "truncated": state.truncated,
        "max_lines": state.request.max_lines,
        "duration_sec": state.duration_sec,
        "skipped_stages": [
            {"stage": f.stage, "error": f.error} for f in state.failures
        ],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
