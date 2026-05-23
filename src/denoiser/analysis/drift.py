"""
Run Comparison & Drift Detection Engine — Phase 2, Task 16.

Compares two analysis runs to surface behavioural changes in the log
stream over time. This is the canonical feature that separates a
one-shot log scanner from a continuous observability platform — equivalent
to Datadog's "compare" view and PagerDuty's Change Events.

Drift signals detected
----------------------
EMERGED       A cluster pattern appeared in run B that did not exist in run A.
RESOLVED      A cluster pattern present in run A disappeared from run B.
ESCALATED     A cluster's severity priority moved upward  (e.g. P2 → P0).
DE_ESCALATED  A cluster's severity priority moved downward (e.g. P1 → P3).
VOLUME_SURGE  A matched cluster's log volume grew by ≥ 50%.
VOLUME_DROP   A matched cluster's log volume shrank by ≥ 50%.
ANOMALY_SPIKE An anomaly score increased by ≥ 0.15 on a matched cluster.
STABLE        A matched cluster shows no significant change.

Cluster matching
----------------
Templates are matched between runs using cosine similarity on their
normalized template strings (Jaccard overlap of token sets). This
handles minor phrasing variations without requiring the same cluster IDs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ── Enumerations ──────────────────────────────────────────────────────────────

class DriftKind(str, Enum):
    EMERGED        = "emerged"
    RESOLVED       = "resolved"
    ESCALATED      = "escalated"
    DE_ESCALATED   = "de_escalated"
    VOLUME_SURGE   = "volume_surge"
    VOLUME_DROP    = "volume_drop"
    ANOMALY_SPIKE  = "anomaly_spike"
    STABLE         = "stable"


# ── Priority ordering ─────────────────────────────────────────────────────────

_PRIORITY_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "unknown": 4}


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ClusterSnapshot:
    """Lightweight serialisable representation of a cluster stored per run."""
    cluster_id: int
    template: str
    size: int
    anomaly_score: float
    priority: str
    composite_severity_score: float
    keyword_flag: bool
    summary: str = ""

    def tokens(self) -> frozenset[str]:
        """Token set for Jaccard similarity matching."""
        return frozenset(re.findall(r"[a-zA-Z0-9]+", self.template.lower()))


@dataclass
class DriftEvent:
    """A single detected behavioural change between two runs.

    Attributes
    ----------
    kind : DriftKind
        The type of drift detected.
    cluster_a : ClusterSnapshot | None
        The matching cluster from run A (baseline). None for EMERGED.
    cluster_b : ClusterSnapshot | None
        The matching cluster from run B (current). None for RESOLVED.
    delta_volume : float
        Fractional volume change  (cluster_b.size - cluster_a.size) / cluster_a.size.
        0.0 for EMERGED / RESOLVED events.
    delta_anomaly : float
        Signed anomaly score change (cluster_b - cluster_a). 0.0 for non-applicable events.
    priority_before : str
        Priority label in run A.
    priority_after : str
        Priority label in run B.
    description : str
        Human-readable one-line description of the change.
    severity : str
        CRITICAL / HIGH / MEDIUM / LOW — impact rating of the drift event itself.
    """
    kind: DriftKind
    cluster_a: ClusterSnapshot | None
    cluster_b: ClusterSnapshot | None
    delta_volume: float = 0.0
    delta_anomaly: float = 0.0
    priority_before: str = "unknown"
    priority_after: str = "unknown"
    description: str = ""
    severity: str = "LOW"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "cluster_a": _snap_to_dict(self.cluster_a),
            "cluster_b": _snap_to_dict(self.cluster_b),
            "delta_volume": round(self.delta_volume, 4),
            "delta_anomaly": round(self.delta_anomaly, 4),
            "priority_before": self.priority_before,
            "priority_after": self.priority_after,
            "description": self.description,
            "severity": self.severity,
        }


@dataclass
class DriftReport:
    """Summary report produced by DriftDetector.compare().

    Attributes
    ----------
    run_id_a : str
    run_id_b : str
    total_clusters_a : int
    total_clusters_b : int
    matched_pairs : int
    events : list[DriftEvent]
    health_delta : float
        Signed change in aggregate health score (−1..+1). Positive = improved.
    summary : str
        One-paragraph plain-English executive summary.
    generated_at : str
    """
    run_id_a: str
    run_id_b: str
    total_clusters_a: int
    total_clusters_b: int
    matched_pairs: int
    events: list[DriftEvent]
    health_delta: float
    summary: str
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        emerged    = [e for e in self.events if e.kind == DriftKind.EMERGED]
        resolved   = [e for e in self.events if e.kind == DriftKind.RESOLVED]
        escalated  = [e for e in self.events if e.kind == DriftKind.ESCALATED]
        de_esc     = [e for e in self.events if e.kind == DriftKind.DE_ESCALATED]
        surges     = [e for e in self.events if e.kind == DriftKind.VOLUME_SURGE]
        drops      = [e for e in self.events if e.kind == DriftKind.VOLUME_DROP]
        spikes     = [e for e in self.events if e.kind == DriftKind.ANOMALY_SPIKE]

        return {
            "run_id_a": self.run_id_a,
            "run_id_b": self.run_id_b,
            "total_clusters_a": self.total_clusters_a,
            "total_clusters_b": self.total_clusters_b,
            "matched_pairs": self.matched_pairs,
            "health_delta": round(self.health_delta, 4),
            "summary": self.summary,
            "generated_at": self.generated_at,
            "counts": {
                "emerged": len(emerged),
                "resolved": len(resolved),
                "escalated": len(escalated),
                "de_escalated": len(de_esc),
                "volume_surge": len(surges),
                "volume_drop": len(drops),
                "anomaly_spike": len(spikes),
                "stable": len(self.events) - len(emerged) - len(resolved)
                          - len(escalated) - len(de_esc) - len(surges) - len(drops) - len(spikes),
            },
            "events": [e.to_dict() for e in self.events],
        }


# ── Utilities ─────────────────────────────────────────────────────────────────

def _snap_to_dict(snap: ClusterSnapshot | None) -> dict | None:
    if snap is None:
        return None
    return {
        "cluster_id": snap.cluster_id,
        "template": snap.template,
        "size": snap.size,
        "anomaly_score": snap.anomaly_score,
        "priority": snap.priority,
        "composite_severity_score": snap.composite_severity_score,
        "keyword_flag": snap.keyword_flag,
        "summary": snap.summary,
    }


def _jaccard(a: frozenset, b: frozenset) -> float:
    """Jaccard token similarity — O(|union|), fast for short template strings."""
    if not a and not b:
        return 1.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _event_severity(kind: DriftKind, priority_before: str, priority_after: str) -> str:
    """Classify the severity of the drift event itself."""
    if kind == DriftKind.ESCALATED and priority_after == "P0":
        return "CRITICAL"
    if kind in (DriftKind.ESCALATED, DriftKind.EMERGED) and priority_after in ("P0", "P1"):
        return "HIGH"
    if kind == DriftKind.ANOMALY_SPIKE:
        return "HIGH"
    if kind in (DriftKind.VOLUME_SURGE, DriftKind.ESCALATED):
        return "MEDIUM"
    if kind in (DriftKind.RESOLVED, DriftKind.DE_ESCALATED, DriftKind.VOLUME_DROP):
        return "LOW"
    return "LOW"


def _health_score(clusters: list[ClusterSnapshot]) -> float:
    """
    Aggregate health score for a run: 0 (bad) → 1 (clean).
    Weighted average of priority and inverse-anomaly-score.
    """
    if not clusters:
        return 1.0
    # P0 (rank 0) -> 0.0. P3 (rank 3) -> 1.0
    rank_scores = [_PRIORITY_RANK.get(c.priority, 3) / 3 for c in clusters]
    anomaly_scores = [1.0 - min(c.anomaly_score, 1.0) for c in clusters]
    return sum(r + a for r, a in zip(rank_scores, anomaly_scores)) / (2 * len(clusters))


# ── Detector ──────────────────────────────────────────────────────────────────

MATCH_THRESHOLD = 0.40        # Minimum Jaccard similarity to consider two clusters a "match"
VOLUME_SURGE_THRESHOLD = 0.50 # ≥50% increase
VOLUME_DROP_THRESHOLD = -0.50 # ≤50% decrease
ANOMALY_SPIKE_THRESHOLD = 0.15


class DriftDetector:
    """Compare two cluster snapshots and surface behavioural drift."""

    def compare(
        self,
        run_id_a: str,
        clusters_a: list[ClusterSnapshot],
        run_id_b: str,
        clusters_b: list[ClusterSnapshot],
    ) -> DriftReport:
        """
        Perform a full drift analysis between two runs.

        Parameters
        ----------
        run_id_a : str   Identifier for run A (baseline / older run).
        clusters_a       Cluster snapshots from run A.
        run_id_b : str   Identifier for run B (current / newer run).
        clusters_b       Cluster snapshots from run B.

        Returns
        -------
        DriftReport
        """
        # Build token sets once
        tokens_a = [(c, c.tokens()) for c in clusters_a]
        tokens_b = [(c, c.tokens()) for c in clusters_b]

        # Greedy best-match pairing (each cluster paired at most once)
        matched_b_ids: set[int] = set()
        matched_a_ids: set[int] = set()
        pairs: list[tuple[ClusterSnapshot, ClusterSnapshot]] = []

        for ca, ta in tokens_a:
            best_sim = MATCH_THRESHOLD
            best_cb = None
            for cb, tb in tokens_b:
                if cb.cluster_id in matched_b_ids:
                    continue
                sim = _jaccard(ta, tb)
                if sim > best_sim:
                    best_sim = sim
                    best_cb = cb
            if best_cb is not None:
                pairs.append((ca, best_cb))
                matched_b_ids.add(best_cb.cluster_id)
                matched_a_ids.add(ca.cluster_id)

        events: list[DriftEvent] = []

        # ── Matched pairs → change analysis ──────────────────────────────────
        for ca, cb in pairs:
            kind = DriftKind.STABLE
            description = f"Pattern stable: '{ca.template[:80]}'"

            delta_volume = (cb.size - ca.size) / max(ca.size, 1)
            delta_anomaly = cb.anomaly_score - ca.anomaly_score

            priority_a = ca.priority
            priority_b = cb.priority
            rank_a = _PRIORITY_RANK.get(priority_a, 4)
            rank_b = _PRIORITY_RANK.get(priority_b, 4)

            # Priority escalation (lower rank = higher severity)
            if rank_b < rank_a:
                kind = DriftKind.ESCALATED
                description = (
                    f"Priority escalated {priority_a} → {priority_b}: "
                    f"'{cb.template[:80]}'"
                )
            elif rank_b > rank_a:
                kind = DriftKind.DE_ESCALATED
                description = (
                    f"Priority improved {priority_a} → {priority_b}: "
                    f"'{cb.template[:80]}'"
                )
            elif delta_volume >= VOLUME_SURGE_THRESHOLD:
                kind = DriftKind.VOLUME_SURGE
                description = (
                    f"Volume surged +{delta_volume * 100:.0f}% "
                    f"({ca.size:,} → {cb.size:,} lines): "
                    f"'{cb.template[:60]}'"
                )
            elif delta_volume <= VOLUME_DROP_THRESHOLD:
                kind = DriftKind.VOLUME_DROP
                description = (
                    f"Volume dropped {delta_volume * 100:.0f}% "
                    f"({ca.size:,} → {cb.size:,} lines): "
                    f"'{cb.template[:60]}'"
                )
            elif delta_anomaly >= ANOMALY_SPIKE_THRESHOLD:
                kind = DriftKind.ANOMALY_SPIKE
                description = (
                    f"Anomaly score spiked +{delta_anomaly:.3f} "
                    f"({ca.anomaly_score:.3f} → {cb.anomaly_score:.3f}): "
                    f"'{cb.template[:60]}'"
                )

            events.append(DriftEvent(
                kind=kind,
                cluster_a=ca,
                cluster_b=cb,
                delta_volume=delta_volume,
                delta_anomaly=delta_anomaly,
                priority_before=priority_a,
                priority_after=priority_b,
                description=description,
                severity=_event_severity(kind, priority_a, priority_b),
            ))

        # ── Unmatched in A → RESOLVED ─────────────────────────────────────────
        for ca, _ in tokens_a:
            if ca.cluster_id not in matched_a_ids:
                events.append(DriftEvent(
                    kind=DriftKind.RESOLVED,
                    cluster_a=ca,
                    cluster_b=None,
                    priority_before=ca.priority,
                    priority_after="resolved",
                    description=f"Pattern resolved (disappeared): '{ca.template[:80]}'",
                    severity=_event_severity(DriftKind.RESOLVED, ca.priority, "resolved"),
                ))

        # ── Unmatched in B → EMERGED ──────────────────────────────────────────
        for cb, _ in tokens_b:
            if cb.cluster_id not in matched_b_ids:
                events.append(DriftEvent(
                    kind=DriftKind.EMERGED,
                    cluster_a=None,
                    cluster_b=cb,
                    priority_before="none",
                    priority_after=cb.priority,
                    description=f"New pattern emerged ({cb.priority}): '{cb.template[:80]}'",
                    severity=_event_severity(DriftKind.EMERGED, "none", cb.priority),
                ))

        # ── Sort by severity then kind priority ───────────────────────────────
        _SEV_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        _KIND_RANK = {DriftKind.ESCALATED: 0, DriftKind.EMERGED: 1, DriftKind.ANOMALY_SPIKE: 2,
                      DriftKind.VOLUME_SURGE: 3, DriftKind.RESOLVED: 4,
                      DriftKind.DE_ESCALATED: 5, DriftKind.VOLUME_DROP: 6, DriftKind.STABLE: 7}
        events.sort(key=lambda e: (_SEV_RANK.get(e.severity, 9), _KIND_RANK.get(e.kind, 9)))

        # ── Health delta ──────────────────────────────────────────────────────
        health_a = _health_score(clusters_a)
        health_b = _health_score(clusters_b)
        health_delta = health_b - health_a

        # ── Executive summary ─────────────────────────────────────────────────
        summary = self._summarise(events, health_delta, run_id_a, run_id_b)

        return DriftReport(
            run_id_a=run_id_a,
            run_id_b=run_id_b,
            total_clusters_a=len(clusters_a),
            total_clusters_b=len(clusters_b),
            matched_pairs=len(pairs),
            events=events,
            health_delta=health_delta,
            summary=summary,
        )

    @staticmethod
    def _summarise(
        events: list[DriftEvent],
        health_delta: float,
        run_id_a: str,
        run_id_b: str,
    ) -> str:
        emerged   = sum(1 for e in events if e.kind == DriftKind.EMERGED)
        resolved  = sum(1 for e in events if e.kind == DriftKind.RESOLVED)
        escalated = sum(1 for e in events if e.kind == DriftKind.ESCALATED)
        spikes    = sum(1 for e in events if e.kind == DriftKind.ANOMALY_SPIKE)
        surges    = sum(1 for e in events if e.kind == DriftKind.VOLUME_SURGE)

        health_desc = (
            "system health improved" if health_delta > 0.05
            else "system health degraded" if health_delta < -0.05
            else "system health remained stable"
        )

        parts = [f"Between run {run_id_a} and {run_id_b}, {health_desc} (Δ{health_delta:+.2f})."]

        if escalated:
            parts.append(f"{escalated} cluster(s) escalated in priority — immediate SRE review required.")
        if emerged:
            parts.append(f"{emerged} new failure pattern(s) emerged.")
        if resolved:
            parts.append(f"{resolved} pattern(s) resolved since the previous run.")
        if spikes:
            parts.append(f"{spikes} cluster(s) showed significant anomaly score increases.")
        if surges:
            parts.append(f"{surges} cluster(s) experienced a volume surge of ≥50%.")
        if not (emerged or resolved or escalated or spikes or surges):
            parts.append("No significant drift detected — log behaviour is consistent between runs.")

        return " ".join(parts)
