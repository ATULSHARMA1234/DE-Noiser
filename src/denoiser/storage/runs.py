"""How an analysis run is shaped and recorded — in one place, for every caller.

There used to be two histories. The Celery worker wrote tenant-scoped
`AnalysisRun` rows that the API serves; the CLI wrote `AnalysisRecord` and
`ClusterRecord` into a second SQLite file of its own (`data/cli_history.db`,
`denoiser.storage.database`), with no tenant on either model and no reader
anywhere — nothing ever queried it back. Two schemas for the same event, and the
one a terminal user produced was invisible to the product.

They are one schema now. A run analysed from the terminal is an `AnalysisRun`
like any other; it simply has no organisation, which is the same unassigned
bucket every other unowned row lands in.

The cluster snapshot shape lives here too. It is the contract between the
pipeline and every reader of a run — the runs API, the topology chart, the issue
upsert — and it was previously typed out inside the worker task, where the CLI
could not reach it and so recorded a different, thinner shape instead.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from denoiser.storage.db import AnalysisRun, Incident

#: Priority ordering, most severe first.
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def format_clusters(
    clusters: list[Any],
    anomalies: dict | None = None,
    summaries: list[str] | None = None,
) -> list[dict]:
    """The stored shape of a run's clusters.

    ``anomalies`` maps a representative template to a scoring result; clusters
    absent from it are ``known`` with a zero distance, which is what "we had a
    baseline and this matched it" means.
    """
    summaries = summaries or []
    formatted: list[dict] = []

    for i, c in enumerate(clusters):
        cluster_data = {
            "id": c.cluster_id,
            "cluster_id": c.cluster_id,
            "size": c.size,
            "summary": summaries[i] if i < len(summaries) else "Analyzing...",
            "source": f"{c.representative_source}:{c.representative_line}",
            "representative_log": c.representative_raw,
            "representative_template": c.representative_template,
            "representative_timestamp_ms": getattr(c, "representative_timestamp_ms", 0),
            # The clusterer's UMAP coordinates (up to 50 points per cluster).
            # Dropping them here meant the Neural Topology chart never received
            # a real projection and silently fell back to a synthetic scatter.
            "projection_2d": [list(point) for point in (getattr(c, "projection_2d", None) or [])],
            "anomaly_label": "known",
            "anomaly_score": 0.0,
        }
        if anomalies and c.representative_template in anomalies:
            res = anomalies[c.representative_template]
            cluster_data["anomaly_label"] = res.label.value
            cluster_data["anomaly_score"] = res.distance

        formatted.append(cluster_data)

    return formatted


def worst_priority(clusters: list[dict]) -> str:
    """The most severe priority among clusters (P0 worst … P3 least).

    A rank-based min correctly surfaces P2 as the worst when no P0/P1 is
    present — the loop this replaced only ever promoted to P0/P1, so a
    top-severity of P2 stayed P3 and never alerted despite the P2 alert gate.
    """
    return min(
        (c.get("priority", "P3") for c in clusters),
        key=lambda p: _PRIORITY_RANK.get(p, 3),
        default="P3",
    )


def record_run(
    db: Session,
    *,
    run_id: str,
    tenant_id: int | None,
    source: str,
    raw_lines: int,
    clusters_snapshot: list[dict],
    duration_sec: float,
    status: str = "Completed",
) -> AnalysisRun:
    """Stage one run on ``db``. The caller owns the commit.

    ``reduction_ratio`` is derived rather than passed: it is a function of the
    other two numbers, and letting callers supply it is how the CLI and the
    worker came to disagree about what it meant.
    """
    cluster_count = len(clusters_snapshot)
    run = AnalysisRun(
        id=run_id,
        tenant_id=tenant_id,
        source=source,
        status=status,
        raw_lines=raw_lines,
        cluster_count=cluster_count,
        reduction_ratio=1.0 - (cluster_count / raw_lines) if raw_lines > 0 else 0,
        duration_sec=duration_sec,
        clusters_snapshot=clusters_snapshot,
    )
    db.add(run)
    return run


def record_intelligence(
    db: Session,
    *,
    run_id: str,
    tenant_id: int | None,
    source: str,
    raw_lines: int,
    cluster_count: int,
    clusters_snapshot: list[dict],
    intelligence: dict,
) -> Incident:
    """Stage the incident a run's LLM summary describes. Caller commits."""
    incident = Incident(
        tenant_id=tenant_id,
        title=intelligence.get("failure_domain", "Unknown Failure"),
        domain=intelligence.get("failure_domain", "System"),
        severity=worst_priority(clusters_snapshot),
        impact_score=min(1.0, cluster_count / 10.0) if cluster_count > 1 else 0.3,
        summary=intelligence.get("incident_summary", ""),
        remediation_hints=intelligence.get("root_cause_hints", []),
        run_id=run_id,
        source=source,
        total_logs=raw_lines,
        cluster_count=cluster_count,
    )
    db.add(incident)
    return incident
