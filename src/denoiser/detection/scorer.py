"""
Out-of-distribution anomaly detection and novelty scoring.
"""

from __future__ import annotations

import numpy as np

from denoiser.baselines.manager import BaselineManager
from denoiser.config import AnomalyLabel, settings
from denoiser.detection.models import AnomalyResult
from denoiser.logging import get_logger

logger = get_logger(__name__)


class AnomalyScorer:
    """Scores log templates against a historical baseline."""

    def __init__(self, baseline_manager: BaselineManager) -> None:
        self.baseline = baseline_manager

        # Load thresholds from config
        self.thresh_low = settings.anomaly_threshold_low
        self.thresh_medium = settings.anomaly_threshold_medium
        self.thresh_high = settings.anomaly_threshold_high

    def _classify(self, distance: float) -> AnomalyLabel:
        """Classify a distance score into an AnomalyLabel based on thresholds."""
        if distance >= self.thresh_high:
            return AnomalyLabel.HIGH_RISK_ANOMALY
        elif distance >= self.thresh_medium:
            return AnomalyLabel.NEW_PATTERN
        elif distance >= self.thresh_low:
            return AnomalyLabel.RARE_KNOWN
        else:
            return AnomalyLabel.KNOWN

    def score_batch(
        self, templates: list[str], vectors: np.ndarray
    ) -> list[AnomalyResult]:
        """Score a batch of vectors against the baseline.

        Parameters
        ----------
        templates : list[str]
            The normalized templates.
        vectors : np.ndarray
            The corresponding embeddings.

        Returns
        -------
        list[AnomalyResult]
            The scoring results for each template.
        """
        if not templates:
            return []

        logger.debug("Scoring batch against baseline", extra={"count": len(templates)})

        # Search the baseline (k=1 nearest neighbor)
        # Returns list of lists of dicts: [ [ {"_distance": 0.1, "cluster_id": 5, ...} ] ]
        search_results = self.baseline.search(vectors, k=1)

        results: list[AnomalyResult] = []
        for i, res_list in enumerate(search_results):
            template = templates[i]

            if not res_list:
                # Edge case: empty baseline
                results.append(
                    AnomalyResult(
                        template=template,
                        distance=1.0,  # Max distance
                        label=AnomalyLabel.HIGH_RISK_ANOMALY,
                    )
                )
                continue

            nearest = res_list[0]
            # LanceDB distance depends on the metric used during table creation (default L2).
            # For cosine, LanceDB returns 1 - cosine_similarity.
            # We assume distance is already normalized 0.0 -> 1.0 or greater.
            distance = float(nearest.get("_distance", 1.0))

            label = self._classify(distance)

            results.append(
                AnomalyResult(
                    template=template,
                    distance=distance,
                    label=label,
                    nearest_cluster_id=nearest.get("cluster_id"),
                    nearest_template=nearest.get("representative_template"),
                )
            )

        return results
