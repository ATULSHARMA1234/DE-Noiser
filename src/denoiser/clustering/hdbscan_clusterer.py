"""
Hybrid semantic clustering engine.

Uses Agglomerative clustering (cosine distance) for small template sets where
HDBSCAN struggles, and HDBSCAN for large-scale datasets where it excels.
"""

from __future__ import annotations

import hdbscan
import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_distances

from denoiser.clustering.models import Cluster
from denoiser.config import settings
from denoiser.exceptions import ClusteringError
from denoiser.ingestion.models import LogRecord
from denoiser.logging import get_logger

logger = get_logger(__name__)

# Templates below this count use Agglomerative; above use HDBSCAN.
_AGGLOMERATIVE_THRESHOLD = 50

# Cosine distance threshold for Agglomerative clustering.
# 0.3 was empirically validated: groups "Request processed successfully"
# across services while keeping "Connection timeout" separate from "OOMKilled".
_COSINE_DISTANCE_THRESHOLD = 0.3


class LogClusterer:
    """Clusters normalized log templates using a hybrid strategy.

    - **Small N (≤50 unique templates)**: Agglomerative clustering with
      cosine distance.  Every template is assigned a real cluster — no noise
      bucket — which is what SREs expect when they have a manageable number
      of distinct log patterns.
    - **Large N (>50)**: HDBSCAN with optional neural-sampling for datasets
      exceeding 50 000 templates.
    """

    def __init__(self) -> None:
        self.min_cluster_size = settings.min_cluster_size
        self.min_samples = settings.min_samples
        self.metric = settings.cluster_metric

    def fit_predict(
        self,
        unique_templates: list[str],
        vectors: np.ndarray,
        template_to_records: dict[str, list[LogRecord]],
        template_to_counts: dict[str, int],
    ) -> list[Cluster]:
        """Cluster the provided vectors and extract rich metadata.

        Parameters
        ----------
        unique_templates : list[str]
            The normalized templates corresponding to the rows in `vectors`.
        vectors : np.ndarray
            The 2D array of embeddings.
        template_to_records : dict[str, list[LogRecord]]
            Mapping of template string back to the raw log records.
        template_to_counts : dict[str, int]
            Mapping of template string to total occurrence count.

        Returns
        -------
        list[Cluster]
            A list of Cluster objects, sorted by size descending.
        """
        if not unique_templates or len(unique_templates) != vectors.shape[0]:
            raise ClusteringError("Mismatch between templates and vectors.")

        n_samples = vectors.shape[0]
        print(f"\n[NEURAL ENGINE] Analysis of {n_samples} unique semantic patterns starting...")

        if n_samples < 2:
            logger.info("Single template detected. Returning single cluster.")
            labels = np.array([0])
        elif n_samples <= _AGGLOMERATIVE_THRESHOLD:
            labels = self._agglomerative_cluster(vectors, n_samples)
        else:
            labels = self._hdbscan_cluster(vectors, n_samples)

        try:
            import umap
            logger.info("Computing UMAP 2D projections for Neural Topology...")
            reducer = umap.UMAP(n_components=2, n_neighbors=15, min_dist=0.1, random_state=42)
            projections = reducer.fit_transform(vectors)
        except Exception as e:
            logger.warning(f"Failed to compute UMAP projections: {e}")
            projections = np.zeros((n_samples, 2))

        return self._build_clusters(
            labels, unique_templates, vectors, template_to_records, template_to_counts, projections
        )

    # ------------------------------------------------------------------
    # Clustering strategies
    # ------------------------------------------------------------------

    def _agglomerative_cluster(self, vectors: np.ndarray, n_samples: int) -> np.ndarray:
        """Agglomerative clustering with cosine distance for small datasets.

        Every point is assigned to a real cluster (no noise bucket), which
        produces far more granular and actionable groupings than HDBSCAN
        when there are only a handful of unique templates.
        """
        logger.info(
            "Using Agglomerative clustering (small dataset)",
            extra={
                "samples": n_samples,
                "distance_threshold": _COSINE_DISTANCE_THRESHOLD,
            },
        )
        try:
            dist_matrix = cosine_distances(vectors)
            clusterer = AgglomerativeClustering(
                n_clusters=None,
                metric="precomputed",
                linkage="average",
                distance_threshold=_COSINE_DISTANCE_THRESHOLD,
            )
            labels = clusterer.fit_predict(dist_matrix)
            n_clusters = len(set(labels))
            logger.info(
                f"Agglomerative clustering produced {n_clusters} clusters "
                f"from {n_samples} templates"
            )
            return labels
        except Exception as e:
            logger.warning(f"Agglomerative clustering failed, falling back to HDBSCAN: {e}")
            return self._hdbscan_cluster(vectors, n_samples)

    def _hdbscan_cluster(self, vectors: np.ndarray, n_samples: int) -> np.ndarray:
        """HDBSCAN clustering for medium-to-large datasets."""
        actual_min_cluster_size = min(self.min_cluster_size, max(2, n_samples // 2))
        actual_min_samples = min(self.min_samples, max(1, n_samples - 1))

        logger.info(
            "Running HDBSCAN",
            extra={
                "samples": n_samples,
                "min_cluster_size": actual_min_cluster_size,
                "min_samples": actual_min_samples,
            },
        )

        # --- NEURAL SAMPLING OPTIMIZATION ---
        MAX_SAMPLES = 50000
        if n_samples > MAX_SAMPLES:
            logger.info(
                f"Large dataset detected ({n_samples} templates). "
                f"Using Neural Sampling (50k) for speed."
            )
            indices = np.random.choice(n_samples, MAX_SAMPLES, replace=False)
            train_vectors = vectors[indices]

            clusterer = hdbscan.HDBSCAN(
                min_cluster_size=actual_min_cluster_size,
                min_samples=actual_min_samples,
                metric=self.metric,
                prediction_data=True,
                allow_single_cluster=True,
            )

            try:
                clusterer.fit(train_vectors)
                labels, _strengths = hdbscan.prediction.approximate_predict(clusterer, vectors)
                return labels
            except Exception as e:
                raise ClusteringError(f"Neural Sampling fit failed: {e}") from e
        else:
            clusterer = hdbscan.HDBSCAN(
                min_cluster_size=actual_min_cluster_size,
                min_samples=actual_min_samples,
                metric=self.metric,
                allow_single_cluster=True,
            )
            try:
                return clusterer.fit_predict(vectors)
            except Exception as e:
                raise ClusteringError(f"HDBSCAN clustering failed: {e}") from e

    # ------------------------------------------------------------------
    # Cluster metadata extraction
    # ------------------------------------------------------------------

    def _build_clusters(
        self,
        labels: np.ndarray,
        unique_templates: list[str],
        vectors: np.ndarray,
        template_to_records: dict[str, list[LogRecord]],
        template_to_counts: dict[str, int],
        projections: np.ndarray,
    ) -> list[Cluster]:
        """Convert raw labels into rich Cluster objects."""
        unique_labels = set(labels)
        clusters: list[Cluster] = []

        for cluster_id in unique_labels:
            mask = labels == cluster_id
            cluster_vectors = vectors[mask]
            cluster_templates = [t for t, is_in in zip(unique_templates, mask, strict=False) if is_in]

            # Centroid
            centroid = np.mean(cluster_vectors, axis=0)

            # Representative: closest to centroid
            if cluster_vectors.shape[0] == 1:
                idx_closest = 0
            else:
                distances = np.linalg.norm(cluster_vectors - centroid, axis=1)
                idx_closest = int(np.argmin(distances))

            representative_template = cluster_templates[idx_closest]

            # Source info from the first record of the representative template
            records = template_to_records.get(representative_template, [])
            representative_raw = records[0].raw_text if records else representative_template
            representative_source = records[0].source if records else "-"
            representative_line = records[0].line_number if records else 0
            representative_timestamp_ms = 0
            if records and records[0].timestamp:
                representative_timestamp_ms = int(records[0].timestamp.timestamp() * 1000)

            # Total raw log lines across all templates in this cluster
            total_size = sum(template_to_counts.get(t, 0) for t in cluster_templates)

            cluster_projections = projections[mask]
            proj_list = [
                [float(row[0]), float(row[1])]
                for row in cluster_projections[:50]
            ]

            clusters.append(
                Cluster(
                    cluster_id=int(cluster_id),
                    centroid=centroid,
                    size=total_size,
                    representative_template=representative_template,
                    representative_raw=representative_raw,
                    representative_source=representative_source,
                    representative_line=representative_line,
                    representative_timestamp_ms=representative_timestamp_ms,
                    templates=cluster_templates,
                    projection_2d=proj_list,
                )
            )

        logger.info(
            "Clustering complete",
            extra={
                "clusters_found": len(unique_labels) - (1 if -1 in unique_labels else 0),
                "noise_templates": sum(1 for c in clusters if c.cluster_id == -1),
            },
        )

        return sorted(clusters, key=lambda c: c.size, reverse=True)
