"""
Task 7: Unit tests for HDBSCAN semantic clustering.

This environment may not have `hdbscan` installed, so tests are skipped if the
dependency is unavailable.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import numpy as np
import pytest

hdbscan = pytest.importorskip("hdbscan")  # noqa: F401

from denoiser.clustering.hdbscan_clusterer import LogClusterer
from denoiser.clustering.models import Cluster
from denoiser.ingestion.models import LogRecord


def test_hdbscan_clusterer_fit_predict_finds_non_noise_clusters() -> None:
    # Two well-separated groups to encourage at least one non-noise cluster.
    # LogClusterer min_cluster_size default is 5.
    n_per_group = 6
    dim = 2
    total = n_per_group * 2

    rng = np.random.default_rng(42)
    center_a = np.array([0.0, 0.0])
    center_b = np.array([10.0, 10.0])

    vectors_a = center_a + rng.normal(scale=0.2, size=(n_per_group, dim))
    vectors_b = center_b + rng.normal(scale=0.2, size=(n_per_group, dim))
    vectors = np.vstack([vectors_a, vectors_b]).astype(np.float32)

    unique_templates = [f"template_{i}" for i in range(total)]
    template_to_records: dict[str, list[LogRecord]] = {}
    template_to_counts: dict[str, int] = {}

    base_time = datetime(2026, 5, 22, 23, 0, 0, tzinfo=timezone.utc)

    for i, t in enumerate(unique_templates):
        svc = "service_a" if i < n_per_group else "service_b"
        template_to_counts[t] = 1
        template_to_records[t] = [
            LogRecord(
                raw_text=t,
                source=f"{svc}.log",
                line_number=i,
                timestamp=base_time + timedelta(seconds=i),
                metadata={"source_label": svc},
            )
        ]

    clusterer = LogClusterer()
    clusters = clusterer.fit_predict(unique_templates, vectors, template_to_records, template_to_counts)

    assert isinstance(clusters, list)
    assert all(isinstance(c, Cluster) for c in clusters)

    # At least one non-noise cluster should be detected.
    non_noise = [c for c in clusters if c.cluster_id != -1]
    assert non_noise, "Expected at least one non-noise cluster."
    assert any(c.size >= 5 for c in non_noise), "Expected a cluster with size >= 5."

    # Representative fields should be populated for non-noise clusters.
    for c in non_noise:
        assert c.representative_template in unique_templates
        assert c.representative_source != "-"

