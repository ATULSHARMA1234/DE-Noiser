"""
Tests for the DriftDetector (Task 16).
"""
from __future__ import annotations

import pytest

from denoiser.analysis.drift import (
    ClusterSnapshot,
    DriftDetector,
    DriftKind,
    _jaccard,
    _health_score,
)


def _make_snap(
    cid: int,
    tmpl: str,
    size: int = 100,
    anomaly: float = 0.2,
    priority: str = "P3"
) -> ClusterSnapshot:
    return ClusterSnapshot(
        cluster_id=cid,
        template=tmpl,
        size=size,
        anomaly_score=anomaly,
        priority=priority,
        composite_severity_score=0.1,
        keyword_flag=False,
    )


class TestDriftDetector:

    def test_jaccard_similarity(self):
        a = frozenset(["connection", "failed", "to", "db"])
        b = frozenset(["connection", "failed", "to", "db", "timeout"])
        assert 0.7 < _jaccard(a, b) < 0.9

        c = frozenset(["success", "login", "user"])
        assert _jaccard(a, c) == 0.0

    def test_health_score_perfect(self):
        clusters = [
            _make_snap(1, "test", priority="P3", anomaly=0.0),
            _make_snap(2, "test", priority="P3", anomaly=0.0)
        ]
        assert _health_score(clusters) == 1.0

    def test_health_score_poor(self):
        clusters = [
            _make_snap(1, "test", priority="P0", anomaly=1.0),
        ]
        # P0 rank = 0 -> 0.0
        # anomaly = 1.0 -> 1.0 - 1.0 = 0.0
        # sum = 0.0 / 2 = 0.0
        assert _health_score(clusters) == 0.0

    def test_emerged_event(self):
        detector = DriftDetector()
        run_a = []
        run_b = [_make_snap(1, "New error pattern emerged in prod", priority="P1")]
        
        report = detector.compare("A", run_a, "B", run_b)
        assert len(report.events) == 1
        assert report.events[0].kind == DriftKind.EMERGED
        assert report.events[0].priority_after == "P1"
        assert report.to_dict()["counts"]["emerged"] == 1

    def test_resolved_event(self):
        detector = DriftDetector()
        run_a = [_make_snap(1, "Old error pattern", priority="P2")]
        run_b = []
        
        report = detector.compare("A", run_a, "B", run_b)
        assert len(report.events) == 1
        assert report.events[0].kind == DriftKind.RESOLVED
        assert report.to_dict()["counts"]["resolved"] == 1

    def test_escalated_event(self):
        detector = DriftDetector()
        run_a = [_make_snap(1, "DB connection slow", priority="P2")]
        run_b = [_make_snap(2, "DB connection slow", priority="P0")]
        
        report = detector.compare("A", run_a, "B", run_b)
        assert len(report.events) == 1
        assert report.events[0].kind == DriftKind.ESCALATED
        assert report.to_dict()["counts"]["escalated"] == 1
        assert report.events[0].severity == "CRITICAL"

    def test_de_escalated_event(self):
        detector = DriftDetector()
        run_a = [_make_snap(1, "DB connection slow", priority="P1")]
        run_b = [_make_snap(2, "DB connection slow", priority="P3")]
        
        report = detector.compare("A", run_a, "B", run_b)
        assert len(report.events) == 1
        assert report.events[0].kind == DriftKind.DE_ESCALATED
        assert report.to_dict()["counts"]["de_escalated"] == 1

    def test_volume_surge(self):
        detector = DriftDetector()
        run_a = [_make_snap(1, "Login failed", size=100)]
        # +100% volume
        run_b = [_make_snap(2, "Login failed", size=200)]
        
        report = detector.compare("A", run_a, "B", run_b)
        assert len(report.events) == 1
        assert report.events[0].kind == DriftKind.VOLUME_SURGE
        assert report.to_dict()["counts"]["volume_surge"] == 1
        assert report.events[0].delta_volume == 1.0

    def test_volume_drop(self):
        detector = DriftDetector()
        run_a = [_make_snap(1, "Login failed", size=100)]
        # -60% volume
        run_b = [_make_snap(2, "Login failed", size=40)]
        
        report = detector.compare("A", run_a, "B", run_b)
        assert len(report.events) == 1
        assert report.events[0].kind == DriftKind.VOLUME_DROP
        assert report.to_dict()["counts"]["volume_drop"] == 1
        assert report.events[0].delta_volume == -0.6

    def test_anomaly_spike(self):
        detector = DriftDetector()
        run_a = [_make_snap(1, "Weird log", anomaly=0.1)]
        # +0.2 spike
        run_b = [_make_snap(2, "Weird log", anomaly=0.3)]
        
        report = detector.compare("A", run_a, "B", run_b)
        assert len(report.events) == 1
        assert report.events[0].kind == DriftKind.ANOMALY_SPIKE
        assert report.to_dict()["counts"]["anomaly_spike"] == 1

    def test_stable_event(self):
        detector = DriftDetector()
        run_a = [_make_snap(1, "Everything is fine", size=100, anomaly=0.1, priority="P3")]
        # Minor changes below thresholds
        run_b = [_make_snap(2, "Everything is fine", size=110, anomaly=0.15, priority="P3")]
        
        report = detector.compare("A", run_a, "B", run_b)
        assert len(report.events) == 1
        assert report.events[0].kind == DriftKind.STABLE
        assert report.to_dict()["counts"]["stable"] == 1

    def test_executive_summary_generation(self):
        detector = DriftDetector()
        run_a = [
            _make_snap(1, "Old error", priority="P2"),
            _make_snap(2, "Surging error", size=100),
            _make_snap(3, "Stable log")
        ]
        run_b = [
            _make_snap(2, "Surging error", size=300), # Surge
            _make_snap(3, "Stable log"),              # Stable
            _make_snap(4, "New fatal error", priority="P0") # Emerged
        ]
        
        report = detector.compare("run_foo", run_a, "run_bar", run_b)
        assert "1 new failure pattern(s) emerged" in report.summary
        assert "1 pattern(s) resolved" in report.summary
        assert "1 cluster(s) experienced a volume surge" in report.summary
