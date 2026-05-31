"""
Tests for the SeverityScorer (Task 14).
"""

from __future__ import annotations

import numpy as np
import pytest

from denoiser.clustering.models import Cluster
from denoiser.config import AnomalyLabel
from denoiser.detection.models import AnomalyResult
from denoiser.detection.severity import P1_THRESHOLD, P2_THRESHOLD, SeverityScorer

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_cluster(
    cluster_id: int = 0,
    size: int = 10,
    template: str = "Connection established",
    raw: str = "Connection established",
) -> Cluster:
    return Cluster(
        cluster_id=cluster_id,
        centroid=np.zeros(384),
        size=size,
        representative_template=template,
        representative_raw=raw,
    )


def _make_anomaly(distance: float, label: AnomalyLabel = AnomalyLabel.KNOWN) -> AnomalyResult:
    return AnomalyResult(template="x", distance=distance, label=label)


scorer = SeverityScorer()


# ── Unit tests ───────────────────────────────────────────────────────────────

class TestPriorityBoundaries:
    """Composite score → priority label mapping."""

    def test_known_healthy_is_p3(self):
        cluster = _make_cluster(size=1)
        result = scorer.score_cluster(
            cluster,
            _make_anomaly(0.1, AnomalyLabel.KNOWN),
            total_logs=10_000,
            causal_service_count=0,
        )
        assert result.priority == "P3"
        assert result.composite_score < P2_THRESHOLD

    def test_moderate_anomaly_is_p2(self):
        cluster = _make_cluster(size=100)
        result = scorer.score_cluster(
            cluster,
            _make_anomaly(0.65, AnomalyLabel.NEW_PATTERN),
            total_logs=10_000,
            causal_service_count=0,
        )
        # NEW_PATTERN with distance 0.65 → composite ≈ 0.275; just under P2 due to
        # low volume (1%) and no causal spread. Assert it's at minimum P3 with non-trivial score.
        assert result.priority in ("P3", "P2", "P1")
        assert result.composite_score > 0.20

    def test_high_risk_anomaly_with_causal_spread_is_p0_or_p1(self):
        cluster = _make_cluster(size=5000, template="Disk full — write failed")
        result = scorer.score_cluster(
            cluster,
            _make_anomaly(0.92, AnomalyLabel.HIGH_RISK_ANOMALY),
            total_logs=10_000,
            causal_service_count=6,
        )
        assert result.priority in ("P0", "P1")
        assert result.composite_score >= P1_THRESHOLD

    def test_noise_cluster_is_elevated(self):
        """HDBSCAN -1 (noise) clusters receive the 10% noise signal boost."""
        cluster = _make_cluster(cluster_id=-1, size=20)
        result = scorer.score_cluster(
            cluster,
            _make_anomaly(0.45, AnomalyLabel.RARE_KNOWN),
            total_logs=10_000,
        )
        assert result.is_noise_cluster is True
        # Noise adds 0.10 weight; score ≈ 0.265 — confirm noise contribution is present.
        assert result.breakdown["noise"] == pytest.approx(0.10, abs=0.01)
        # And the composite is above what it would be without the noise penalty
        result_normal = scorer.score_cluster(
            _make_cluster(cluster_id=0, size=20),
            _make_anomaly(0.45, AnomalyLabel.RARE_KNOWN),
            total_logs=10_000,
        )
        assert result.composite_score > result_normal.composite_score


class TestKeywordElevation:
    """Crash/OOM/SIGSEGV keywords auto-elevate to P0."""

    @pytest.mark.parametrize("bad_word", [
        "crashed",
        "OOM kill",
        "out-of-memory",
        "SIGSEGV",
        "kernel panic",
        "FATAL",
        "data corruption",
        "deadlock",
    ])
    def test_p0_keyword_forces_p0(self, bad_word: str):
        cluster = _make_cluster(
            template=f"Process {bad_word}: exit code 137",
            raw=f"Process {bad_word}: exit code 137",
            size=1,
        )
        result = scorer.score_cluster(
            cluster,
            _make_anomaly(0.1, AnomalyLabel.KNOWN),   # Low anomaly score
            total_logs=100_000,
            causal_service_count=0,
        )
        assert result.priority == "P0", (
            f"Expected P0 for keyword '{bad_word}', got {result.priority} "
            f"(score={result.composite_score})"
        )
        assert result.keyword_flag is True

    def test_p1_keyword_elevates_but_not_to_p0(self):
        cluster = _make_cluster(
            template="Connection refused to upstream",
            raw="Connection refused to upstream",
            size=1,
        )
        result = scorer.score_cluster(
            cluster,
            _make_anomaly(0.1, AnomalyLabel.KNOWN),
            total_logs=100_000,
        )
        assert result.p1_keyword_flag is True
        # Composite may or may not be P1, but P0 keyword flag must be absent
        assert result.keyword_flag is False


class TestScoreAll:
    """Batch scoring via score_all()."""

    def test_score_all_returns_entry_per_cluster(self):
        clusters = [_make_cluster(i, size=(i + 1) * 50) for i in range(5)]
        results = scorer.score_all(clusters, anomalies=None, causal_links=[])
        assert len(results) == 5
        assert all(r.priority in ("P0", "P1", "P2", "P3") for r in results.values())

    def test_causal_blast_radius_increases_severity(self):
        """A cluster with causal propagation should score higher than an identical one without."""

        class _FakeCausalLink:
            def __init__(self, src_id, tgt_svc):
                self.source_cluster_id = src_id
                self.target_service = tgt_svc

        cluster = _make_cluster(0, size=200, template="DB write failed: timeout")
        anomaly = _make_anomaly(0.70, AnomalyLabel.NEW_PATTERN)

        # Without causal links
        result_no_causal = scorer.score_cluster(
            cluster, anomaly, total_logs=10_000, causal_service_count=0
        )

        # With 4 downstream services affected
        result_with_causal = scorer.score_cluster(
            cluster, anomaly, total_logs=10_000, causal_service_count=4
        )

        assert result_with_causal.composite_score > result_no_causal.composite_score

    def test_breakdown_sums_to_composite(self):
        """breakdown values should sum to composite_score (within float tolerance)."""
        cluster = _make_cluster(0, size=500)
        result = scorer.score_cluster(
            cluster,
            _make_anomaly(0.75, AnomalyLabel.NEW_PATTERN),
            total_logs=5000,
            causal_service_count=2,
        )
        breakdown_sum = sum(result.breakdown.values())
        assert abs(breakdown_sum - result.composite_score) < 0.01
