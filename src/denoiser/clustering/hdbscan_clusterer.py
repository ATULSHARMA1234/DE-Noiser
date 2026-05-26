"""
HDBSCAN-based semantic clustering engine.
"""

from __future__ import annotations

import hdbscan
import numpy as np

from denoiser.clustering.models import Cluster
from denoiser.config import settings
from denoiser.exceptions import ClusteringError
from denoiser.ingestion.models import LogRecord
from denoiser.logging import get_logger

logger = get_logger(__name__)


class LogClusterer:
    """Clusters normalized log templates using HDBSCAN."""

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

        Returns
        -------
        list[Cluster]
            A list of Cluster objects, including the noise cluster (-1) if present.
        """
        if not unique_templates or len(unique_templates) != vectors.shape[0]:
            raise ClusteringError("Mismatch between templates and vectors.")

        # If we have very few unique templates, HDBSCAN might fail or flag everything as noise.
        # Adjust min_cluster_size dynamically if needed, or fallback gracefully.
        n_samples = vectors.shape[0]
        print(f"\n[NEURAL ENGINE] Analysis of {n_samples} unique semantic patterns starting...")

        if n_samples < 2:
            logger.info("Single template detected. Bypassing HDBSCAN and returning single cluster.")
            labels = np.array([0])
        else:
            actual_min_cluster_size = min(self.min_cluster_size, max(2, n_samples // 2))
            if n_samples < 2:
                actual_min_cluster_size = 2

            actual_min_samples = min(self.min_samples, max(1, n_samples - 1))
            if n_samples <= 1:
                actual_min_samples = 1

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
                logger.info(f"Large dataset detected ({n_samples} templates). Using Neural Sampling (50k) for speed.")
                # Pick a random sample for training the clusterer
                indices = np.random.choice(n_samples, MAX_SAMPLES, replace=False)
                train_vectors = vectors[indices]
                
                clusterer = hdbscan.HDBSCAN(
                    min_cluster_size=actual_min_cluster_size,
                    min_samples=actual_min_samples,
                    metric=self.metric,
                    prediction_data=True, # Critical for assigning labels to non-sampled data
                    allow_single_cluster=True,
                )
                
                try:
                    clusterer.fit(train_vectors)
                    # Assign labels to ALL vectors based on the sampled model
                    labels, strengths = hdbscan.prediction.approximate_predict(clusterer, vectors)
                except Exception as e:
                    raise ClusteringError(f"Neural Sampling fit failed: {e}") from e
            else:
                # Standard path for smaller datasets
                clusterer = hdbscan.HDBSCAN(
                    min_cluster_size=actual_min_cluster_size,
                    min_samples=actual_min_samples,
                    metric=self.metric,
                    allow_single_cluster=True,
                )
                try:
                    labels = clusterer.fit_predict(vectors)
                except Exception as e:
                    raise ClusteringError(f"HDBSCAN clustering failed: {e}") from e

        # Extract metadata
        unique_labels = set(labels)
        clusters: list[Cluster] = []

        for cluster_id in unique_labels:
            # Boolean mask for the current cluster
            mask = labels == cluster_id
            
            # Subsets for this cluster
            cluster_vectors = vectors[mask]
            cluster_templates = [t for t, is_in in zip(unique_templates, mask) if is_in]
            
            # Calculate centroid
            centroid = np.mean(cluster_vectors, axis=0)
            
            # Find representative example (closest to centroid)
            if cluster_vectors.shape[0] == 1:
                idx_closest = 0
            else:
                # Euclidean distance to centroid
                distances = np.linalg.norm(cluster_vectors - centroid, axis=1)
                idx_closest = int(np.argmin(distances))
                
            representative_template = cluster_templates[idx_closest]
            
            # Get source info for this representative template
            records = template_to_records.get(representative_template, [])
            representative_raw = records[0].raw_text if records else representative_template
            representative_source = records[0].source if records else "-"
            representative_line = records[0].line_number if records else 0
            representative_timestamp_ms = 0
            if records and records[0].timestamp:
                # Store epoch milliseconds for fast metrics correlation.
                representative_timestamp_ms = int(records[0].timestamp.timestamp() * 1000)
            
            # Calculate total size (total raw log lines, using our new counter)
            total_size = sum(template_to_counts.get(t, 0) for t in cluster_templates)

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
