"""
Data models for the clustering layer.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np


@dataclasses.dataclass(slots=True)
class Cluster:
    """Represents a semantically grouped collection of log templates.

    Attributes
    ----------
    cluster_id : int
        The integer ID assigned by the clustering algorithm. -1 indicates noise/anomalies.
    centroid : np.ndarray
        The geometric center of the cluster in vector space.
    size : int
        The total number of raw log lines that map to this cluster.
    representative_template : str
        The normalized template closest to the centroid, used for display.
    representative_raw : str
        A raw log line corresponding to the representative template.
    templates : list[str]
        All unique normalized templates in this cluster.
    """

    cluster_id: int
    centroid: np.ndarray
    size: int
    representative_template: str
    representative_raw: str
    representative_source: str = "-"
    representative_line: int = 0
    templates: list[str] = dataclasses.field(default_factory=list)
    label: str | None = None  # Human-provided label/name for the cluster
    is_acknowledged: bool = False  # Whether the team has marked this cluster as "known/safe"
    org_id: str | None = None
    team_id: str | None = None

