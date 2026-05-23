"""
Unit tests for Task 11: Temporal proximity causal scorer.
Verifies the CausalScorer math, sliding-window search, and directionality.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, List

from denoiser.clustering.models import Cluster
from denoiser.ingestion.models import LogRecord
from denoiser.detection.causal_scorer import CausalScorer, CausalLink


class TestCausalScorer:
    """Unit tests for the CausalScorer correlation engine."""

    @pytest.fixture
    def sample_data(self) -> tuple[list[Cluster], dict[str, list[LogRecord]]]:
        """Creates dummy clusters and records simulating cross-service causal events.
        
        Service A (payment) has a failure, and 50ms later, Service B (order) 
        has a timeout warning, repeating 3 times.
        """
        # Cluster 0: payment-service errors
        c0 = Cluster(
            cluster_id=0,
            centroid=None,
            size=3,
            representative_template="payment_service failed charging card",
            representative_raw="payment_service failed charging card",
            representative_source="payment.log",
            representative_line=1,
            templates=["payment_service failed charging card"]
        )

        # Cluster 1: order-service warnings
        c1 = Cluster(
            cluster_id=1,
            centroid=None,
            size=3,
            representative_template="order_service payment delayed timeout",
            representative_raw="order_service payment delayed timeout",
            representative_source="order.log",
            representative_line=1,
            templates=["order_service payment delayed timeout"]
        )

        clusters = [c0, c1]

        # Construct timestamps
        base_time = datetime(2026, 5, 22, 23, 0, 0, tzinfo=timezone.utc)
        
        # Service A events at: base_time, base_time + 10s, base_time + 20s
        records_a = [
            LogRecord(
                raw_text="payment_service failed charging card",
                source="payment.log",
                line_number=i,
                timestamp=base_time + timedelta(seconds=i * 10),
                metadata={"source_label": "payment_service"}
            )
            for i in range(3)
        ]

        # Service B events triggered exactly 50ms after Service A
        records_b = [
            LogRecord(
                raw_text="order_service payment delayed timeout",
                source="order.log",
                line_number=i,
                timestamp=base_time + timedelta(seconds=i * 10) + timedelta(milliseconds=50),
                metadata={"source_label": "order_service"}
            )
            for i in range(3)
        ]

        template_to_records = {
            "payment_service failed charging card": records_a,
            "order_service payment delayed timeout": records_b
        }

        return clusters, template_to_records

    def test_causal_scorer_detects_link(self, sample_data):
        """Should detect directed causal link A -> B with exact delay and high confidence."""
        clusters, template_to_records = sample_data
        
        scorer = CausalScorer(window_size_ms=500.0)
        links = scorer.analyze(clusters, template_to_records)

        assert len(links) == 1
        link = links[0]

        assert link.source_cluster_id == 0
        assert link.target_cluster_id == 1
        assert link.source_service == "payment_service"
        assert link.target_service == "order_service"
        assert link.occurrences == 3
        # Assert average delay is exactly 50ms
        assert link.avg_delay_ms == pytest.approx(50.0)
        # Direction string must represent lead-lag direction
        assert "0 -> Cluster 1" in link.direction
        # Confidence score should be high (> 0.5)
        assert link.confidence > 0.5

    def test_causal_scorer_ignores_large_delays(self, sample_data):
        """If the delay is larger than the window size, no link should be found."""
        clusters, template_to_records = sample_data
        
        # Modify Service B timestamps so they occur 600ms after Service A (outside the 500ms window)
        for record in template_to_records["order_service payment delayed timeout"]:
            record.timestamp = record.timestamp + timedelta(milliseconds=550) # 50ms + 550ms = 600ms

        scorer = CausalScorer(window_size_ms=500.0)
        links = scorer.analyze(clusters, template_to_records)

        assert len(links) == 0

    def test_causal_scorer_ignores_same_service_events(self, sample_data):
        """Events originating from the same service should be excluded from cross-service analysis."""
        clusters, template_to_records = sample_data
        
        # Modify metadata so both belong to the same service
        for rec in template_to_records["payment_service failed charging card"]:
            rec.metadata["source_label"] = "common_service"
        for rec in template_to_records["order_service payment delayed timeout"]:
            rec.metadata["source_label"] = "common_service"

        scorer = CausalScorer(window_size_ms=500.0)
        links = scorer.analyze(clusters, template_to_records)

        assert len(links) == 0

    def test_causal_scorer_empty_or_small_clusters(self):
        """Fewer than 2 clusters should immediately return empty results."""
        scorer = CausalScorer()
        assert scorer.analyze([], {}) == []
        
        c = Cluster(
            cluster_id=0, centroid=None, size=1,
            representative_template="temp", representative_raw="raw",
            representative_source="src", representative_line=1, templates=["temp"]
        )
        assert scorer.analyze([c], {}) == []
