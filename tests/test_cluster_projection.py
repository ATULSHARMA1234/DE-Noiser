"""The UMAP projection must survive from the clusterer into the API payload.

The clusterer computes 2D coordinates per cluster and the API schema declares
the field, but the worker's response formatter dropped it. The Command Center's
"Neural Topology / HDBSCAN Projection" chart therefore never received real
coordinates and silently rendered a synthetic scatter instead — a fabricated
picture under a heading claiming it was the clustering result.
"""

import numpy as np
import pytest

from denoiser.api.schemas import ClusterResponse
from denoiser.clustering.models import Cluster
from denoiser.storage.runs import format_clusters


def _cluster(cluster_id: int, projection: list[list[float]] | None) -> Cluster:
    return Cluster(
        cluster_id=cluster_id,
        centroid=np.zeros(3),
        size=10,
        representative_template="db connection failed",
        representative_raw="ERROR db connection failed",
        representative_source="app.log",
        representative_line=1,
        representative_timestamp_ms=0,
        templates=["db connection failed"],
        projection_2d=projection or [],
    )


#: The real formatter. This used to be a copy of the worker's inlined loop,
#: which meant the test could only ever agree with itself — it was paired with a
#: grep over the worker source to check the two had not drifted. The formatting
#: now lives in a module both the worker and the CLI call, so the test calls it.
_format = format_clusters


class TestClusterModel:
    def test_clusterer_output_carries_projection(self):
        cluster = _cluster(0, [[1.5, -2.5], [3.0, 4.0]])
        assert cluster.projection_2d == [[1.5, -2.5], [3.0, 4.0]]

    def test_schema_accepts_the_projection_field(self):
        schema = ClusterResponse(
            id=0, cluster_id=0, size=10,
            summary="s", source="app.log:1",
            representative_log="ERROR", representative_template="ERROR",
            projection_2d=[[1.0, 2.0]],
        )
        assert schema.projection_2d == [[1.0, 2.0]]


class TestWorkerPayload:
    def test_projection_reaches_the_api_payload(self):
        payload = _format([_cluster(0, [[1.0, 2.0], [3.0, 4.0]])])
        assert payload[0]["projection_2d"] == [[1.0, 2.0], [3.0, 4.0]]

    def test_cluster_without_projection_yields_empty_list_not_missing_key(self):
        """The UI keys off an empty projection to say so, rather than inventing points."""
        payload = _format([_cluster(1, None)])
        assert payload[0]["projection_2d"] == []

    def test_the_pipeline_and_the_cli_share_this_formatter(self):
        """Neither may re-implement the snapshot shape locally.

        The CLI did exactly that, and recorded a thinner cluster row into its own
        private database as a result.
        """
        from pathlib import Path

        import denoiser.analysis.pipeline as pipeline
        import denoiser.cli.main as cli

        for module in (pipeline, cli):
            source = Path(module.__file__).read_text(encoding="utf-8")
            assert "format_clusters" in source, (
                f"{module.__name__} does not go through the shared formatter"
            )
            assert '"projection_2d"' not in source, (
                f"{module.__name__} is building the cluster snapshot itself again"
            )


@pytest.mark.parametrize("points", [[], [[0.0, 0.0]], [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]])
def test_projection_round_trips_through_json(points):
    import json

    payload = _format([_cluster(0, points)])
    assert json.loads(json.dumps(payload))[0]["projection_2d"] == points


class TestProjectionGeneration:
    """UMAP cannot build a manifold from a handful of templates.

    The old code caught the failure and returned zeros, so every point landed on
    the origin and the chart drew a single dot regardless of the clustering.
    """

    @staticmethod
    def _project(n_samples: int, dim: int = 16):
        from denoiser.clustering.hdbscan_clusterer import LogClusterer

        rng = np.random.default_rng(1234)
        vectors = rng.normal(size=(n_samples, dim))
        return LogClusterer()._project_2d(vectors, n_samples)

    @pytest.mark.parametrize("n_samples", [2, 3, 4])
    def test_small_template_counts_get_distinct_coordinates(self, n_samples):
        projections = self._project(n_samples)
        assert projections.shape == (n_samples, 2)
        assert np.isfinite(projections).all()
        distinct = {tuple(np.round(row, 6)) for row in projections}
        assert len(distinct) == n_samples, "every template must land somewhere of its own"
        assert not np.allclose(projections, 0.0), "collapsing to the origin is the bug"

    def test_single_template_projects_to_the_origin(self):
        projections = self._project(1)
        assert projections.shape == (1, 2)
        assert np.allclose(projections, 0.0)

    def test_larger_sets_project_without_collapsing(self):
        projections = self._project(12)
        assert projections.shape == (12, 2)
        assert np.isfinite(projections).all()
        assert len({tuple(np.round(row, 6)) for row in projections}) > 1

    def test_projection_survives_a_broken_umap(self, monkeypatch):
        """A UMAP failure must degrade to PCA, not to a pile of zeros."""
        import builtins

        real_import = builtins.__import__

        def _no_umap(name, *args, **kwargs):
            if name == "umap":
                raise ImportError("umap unavailable (test)")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_umap)
        projections = self._project(8)
        assert np.isfinite(projections).all()
        assert not np.allclose(projections, 0.0)
