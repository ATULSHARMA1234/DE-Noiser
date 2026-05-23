"""
Severity Triage Scorer — Phase 2, Task 14.

Assigns a deterministic P0/P1/P2/P3 priority label to every log cluster by
combining multiple independent risk signals into a single composite score:

  1. Anomaly distance (out-of-distribution novelty from baseline)
  2. Cluster volume  (blast-radius proxy — affected log count)
  3. Noise-cluster penalty (HDBSCAN id == -1 → unclustered outlier)
  4. Causal blast-radius (how many downstream services are affected)
  5. Keyword elevation (crash / OOM / SIGSEGV / panic → instant P0 flag)

Priority levels
---------------
P0  Severity ≥ 0.80  — Critical. Immediate page-out required.
P1  Severity ≥ 0.55  — High.     Requires SRE attention within 15 min.
P2  Severity ≥ 0.30  — Medium.   Investigate within the current sprint.
P3  Severity < 0.30  — Low/Info. Monitor only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from denoiser.clustering.models import Cluster
from denoiser.config import AnomalyLabel
from denoiser.detection.models import AnomalyResult
from denoiser.logging import get_logger

logger = get_logger(__name__)

# ── Priority thresholds ──────────────────────────────────────────────────────

P0_THRESHOLD = 0.80
P1_THRESHOLD = 0.55
P2_THRESHOLD = 0.30

# ── Keyword patterns that auto-elevate to P0 ────────────────────────────────

_P0_KEYWORDS: re.Pattern = re.compile(
    r"\b(crash(ed)?|oom.?kill|out.?of.?memory|sigsegv|segfault|"
    r"kernel.?panic|fatal|deadlock|data.?loss|data.?corruption|"
    r"disk.?full|filesystem.?full|cannot.?allocate|illegal.?instruction|"
    r"stack.?overflow|heap.?overflow|double.?free|use.?after.?free)\b",
    re.IGNORECASE,
)

_P1_KEYWORDS: re.Pattern = re.compile(
    r"\b(exception|error|failed|timeout|refused|unavailable|unreachable|"
    r"circuit.?breaker|connection.?reset|backpressure|throttl|rate.?limit|"
    r"rollback|abort|panic)\b",
    re.IGNORECASE,
)


# ── Output dataclass ─────────────────────────────────────────────────────────

@dataclass
class SeverityResult:
    """Triage scoring result for a single cluster.

    Attributes
    ----------
    cluster_id : int
        The HDBSCAN cluster ID.
    priority : str
        One of ``P0``, ``P1``, ``P2``, ``P3``.
    composite_score : float
        Normalised composite risk score in [0, 1].
    anomaly_contribution : float
        Contribution from the OOD anomaly distance signal.
    volume_contribution : float
        Contribution from cluster log volume.
    causal_contribution : float
        Contribution from causal blast-radius across services.
    keyword_flag : bool
        True if a P0-level keyword was detected in the representative log.
    p1_keyword_flag : bool
        True if a P1-level keyword was detected.
    is_noise_cluster : bool
        True if the cluster is HDBSCAN's noise bucket (id == -1).
    breakdown : dict[str, float]
        Per-signal breakdown for dashboard display.
    """

    cluster_id: int
    priority: str
    composite_score: float
    anomaly_contribution: float
    volume_contribution: float
    causal_contribution: float
    keyword_flag: bool
    p1_keyword_flag: bool
    is_noise_cluster: bool
    breakdown: dict[str, float]


# ── Scorer ───────────────────────────────────────────────────────────────────

class SeverityScorer:
    """Multi-signal severity triage scorer for log clusters."""

    # Signal weights — must sum to 1.0
    W_ANOMALY = 0.35
    W_VOLUME = 0.20
    W_CAUSAL = 0.25
    W_NOISE = 0.10
    W_KEYWORD = 0.10

    def score_cluster(
        self,
        cluster: Cluster,
        anomaly_result: AnomalyResult | None,
        *,
        total_logs: int = 1,
        causal_service_count: int = 0,
        max_causal_services: int = 10,
    ) -> SeverityResult:
        """Compute a severity triage score for a single cluster.

        Parameters
        ----------
        cluster : Cluster
            The cluster to score.
        anomaly_result : AnomalyResult | None
            The anomaly scoring result for the cluster's representative
            template. May be ``None`` if no baseline exists.
        total_logs : int
            Total log volume across all clusters (used to normalise volume).
        causal_service_count : int
            Number of *distinct* downstream services causally linked from this
            cluster. Higher counts increase severity.
        max_causal_services : int
            Upper bound for normalising causal blast-radius (default 10).

        Returns
        -------
        SeverityResult
        """
        rep_text = f"{cluster.representative_template} {cluster.representative_raw}"

        # ── Signal 1: Anomaly Distance ───────────────────────────────────────
        if anomaly_result is not None:
            raw_distance = float(anomaly_result.distance)
            # AnomalyLabel gives us coarse buckets for extra boost
            label_boost = {
                AnomalyLabel.HIGH_RISK_ANOMALY: 0.15,
                AnomalyLabel.NEW_PATTERN: 0.05,
                AnomalyLabel.RARE_KNOWN: 0.0,
                AnomalyLabel.KNOWN: -0.10,
            }.get(anomaly_result.label, 0.0)
            anomaly_signal = min(1.0, max(0.0, raw_distance + label_boost))
        else:
            # No baseline → treat as moderately suspicious
            anomaly_signal = 0.50

        # ── Signal 2: Volume (blast radius proxy) ────────────────────────────
        cluster_fraction = cluster.size / max(total_logs, 1)
        # Logarithmic scaling: even small fractions of a million-line log are
        # significant; large fractions are critical.
        import math
        volume_signal = min(1.0, math.log1p(cluster_fraction * 100) / math.log1p(100))

        # ── Signal 3: Causal blast-radius ────────────────────────────────────
        causal_signal = min(1.0, causal_service_count / max(max_causal_services, 1))

        # ── Signal 4: Noise-cluster penalty ──────────────────────────────────
        noise_signal = 1.0 if cluster.cluster_id == -1 else 0.0

        # ── Signal 5: Keyword elevation ───────────────────────────────────────
        keyword_flag = bool(_P0_KEYWORDS.search(rep_text))
        p1_keyword_flag = bool(_P1_KEYWORDS.search(rep_text))
        keyword_signal = 1.0 if keyword_flag else (0.5 if p1_keyword_flag else 0.0)

        # ── Composite ─────────────────────────────────────────────────────────
        composite = (
            self.W_ANOMALY  * anomaly_signal  +
            self.W_VOLUME   * volume_signal   +
            self.W_CAUSAL   * causal_signal   +
            self.W_NOISE    * noise_signal    +
            self.W_KEYWORD  * keyword_signal
        )
        composite = min(1.0, max(0.0, composite))

        # Hard override: P0-keyword forces composite ≥ P0 threshold
        if keyword_flag and composite < P0_THRESHOLD:
            composite = P0_THRESHOLD

        # ── Priority label ────────────────────────────────────────────────────
        if composite >= P0_THRESHOLD:
            priority = "P0"
        elif composite >= P1_THRESHOLD:
            priority = "P1"
        elif composite >= P2_THRESHOLD:
            priority = "P2"
        else:
            priority = "P3"

        breakdown = {
            "anomaly":  round(self.W_ANOMALY  * anomaly_signal,  4),
            "volume":   round(self.W_VOLUME   * volume_signal,   4),
            "causal":   round(self.W_CAUSAL   * causal_signal,   4),
            "noise":    round(self.W_NOISE    * noise_signal,    4),
            "keyword":  round(self.W_KEYWORD  * keyword_signal,  4),
        }

        logger.debug(
            "Cluster severity scored",
            extra={
                "cluster_id": cluster.cluster_id,
                "priority": priority,
                "composite": round(composite, 4),
                "breakdown": breakdown,
            },
        )

        return SeverityResult(
            cluster_id=cluster.cluster_id,
            priority=priority,
            composite_score=round(composite, 4),
            anomaly_contribution=breakdown["anomaly"],
            volume_contribution=breakdown["volume"],
            causal_contribution=breakdown["causal"],
            keyword_flag=keyword_flag,
            p1_keyword_flag=p1_keyword_flag,
            is_noise_cluster=cluster.cluster_id == -1,
            breakdown=breakdown,
        )

    def score_all(
        self,
        clusters: list[Cluster],
        anomalies: dict[str, AnomalyResult] | None,
        causal_links: list[Any],
    ) -> dict[int, SeverityResult]:
        """Score all clusters in a single pass.

        Parameters
        ----------
        clusters : list[Cluster]
            All clusters from the current analysis run.
        anomalies : dict[str, AnomalyResult] | None
            Mapping from template string → AnomalyResult.
        causal_links : list
            The list of ``CausalLink`` objects from ``CausalScorer``.

        Returns
        -------
        dict[int, SeverityResult]
            Mapping from cluster_id → SeverityResult.
        """
        total_logs = sum(c.size for c in clusters)
        max_causal = max(len(clusters), 10)

        # Build causal blast-radius map: cluster_id → #distinct downstream services
        blast_radius: dict[int, set[str]] = {}
        for link in causal_links:
            src = link.source_cluster_id
            tgt_svc = link.target_service
            blast_radius.setdefault(src, set()).add(tgt_svc)

        results: dict[int, SeverityResult] = {}
        for cluster in clusters:
            anomaly_result = None
            if anomalies:
                anomaly_result = anomalies.get(cluster.representative_template)

            causal_count = len(blast_radius.get(cluster.cluster_id, set()))

            result = self.score_cluster(
                cluster,
                anomaly_result,
                total_logs=total_logs,
                causal_service_count=causal_count,
                max_causal_services=max_causal,
            )
            results[cluster.cluster_id] = result

        # Log summary
        p0_count = sum(1 for r in results.values() if r.priority == "P0")
        p1_count = sum(1 for r in results.values() if r.priority == "P1")
        logger.info(
            "Severity triage complete",
            extra={
                "total_clusters": len(clusters),
                "P0": p0_count,
                "P1": p1_count,
                "P2": sum(1 for r in results.values() if r.priority == "P2"),
                "P3": sum(1 for r in results.values() if r.priority == "P3"),
            },
        )

        return results
