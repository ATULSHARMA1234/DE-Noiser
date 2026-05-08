"""
Data models for anomaly detection results.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from denoiser.config import AnomalyLabel


@dataclasses.dataclass(slots=True)
class AnomalyResult:
    """Represents the anomaly scoring result for a specific log template.

    Attributes
    ----------
    template : str
        The normalized log template.
    distance : float
        The distance metric to the nearest known baseline centroid.
    label : AnomalyLabel
        The classification (KNOWN, NEW_PATTERN, HIGH_RISK_ANOMALY, etc.).
    nearest_cluster_id : int | None
        The ID of the nearest baseline cluster, if a baseline was used.
    nearest_template : str | None
        The representative template of the nearest baseline cluster.
    """

    template: str
    distance: float
    label: AnomalyLabel
    nearest_cluster_id: int | None = None
    nearest_template: str | None = None
