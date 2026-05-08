"""
Data models for historical baselines.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any


@dataclasses.dataclass(slots=True)
class BaselineMetadata:
    """Metadata describing a stored historical baseline.

    Attributes
    ----------
    version : str
        The version of the baseline format.
    created_at : str
        ISO 8601 timestamp of when the baseline was created.
    embedding_model : str
        The SentenceTransformer model used to generate the embeddings.
    cluster_count : int
        The number of semantic clusters stored in this baseline.
    total_logs_processed : int
        The total number of raw log lines that contributed to this baseline.
    config_snapshot : dict[str, Any]
        A snapshot of the configuration used during creation.
    """

    version: str
    created_at: str
    embedding_model: str
    cluster_count: int
    total_logs_processed: int
    config_snapshot: dict[str, Any]
