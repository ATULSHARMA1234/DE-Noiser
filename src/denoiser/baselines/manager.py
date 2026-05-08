"""
Baseline manager using LanceDB for vector persistence.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa

from denoiser.baselines.models import BaselineMetadata
from denoiser.clustering.models import Cluster
from denoiser.config import settings
from denoiser.exceptions import BaselineError
from denoiser.logging import get_logger

logger = get_logger(__name__)


class BaselineManager:
    """Manages the creation, loading, and querying of historical baselines using LanceDB."""

    # Schema for the LanceDB table storing cluster centroids
    _schema = pa.schema([
        pa.field("cluster_id", pa.int32()),
        pa.field("centroid", pa.list_(pa.float32(), -1)),  # -1 allows variable length depending on embedding_dimension
        pa.field("size", pa.int32()),
        pa.field("representative_template", pa.string()),
        pa.field("representative_raw", pa.string()),
    ])

    def __init__(self, path: Path | str | None = None) -> None:
        """
        Parameters
        ----------
        path : Path | str | None
            The path to the baseline directory (LanceDB dataset).
            If None, uses the default path from settings.
        """
        self.path = Path(path) if path else settings.default_baseline_path
        self.metadata_path = self.path / "metadata.json"
        self._db: lancedb.DBConnection | None = None
        self._table: lancedb.table.Table | None = None

    def _get_db(self) -> lancedb.DBConnection:
        if self._db is None:
            self._db = lancedb.connect(str(self.path))
        return self._db

    def build(self, clusters: list[Cluster], total_logs: int) -> None:
        """Create a new baseline index from a list of clusters.

        Parameters
        ----------
        clusters : list[Cluster]
            The clusters to store (typically excluding the noise cluster -1).
        total_logs : int
            The total raw log count that produced these clusters.
        """
        logger.info("Building baseline index", extra={"path": str(self.path), "clusters": len(clusters)})

        if self.path.exists():
            import shutil
            logger.debug("Removing existing baseline", extra={"path": str(self.path)})
            shutil.rmtree(self.path)

        self.path.mkdir(parents=True)
        db = self._get_db()

        # Filter out noise cluster (-1) if we only want "known good"
        valid_clusters = [c for c in clusters if c.cluster_id != -1]

        # Fix the schema vector dimension dynamically
        dim = valid_clusters[0].centroid.shape[0] if valid_clusters else settings.embedding_dimension
        
        # PyArrow requires explicit sizing for vector similarity search in some versions
        # Let's create a dynamic schema
        dynamic_schema = pa.schema([
            pa.field("cluster_id", pa.int32()),
            pa.field("vector", pa.list_(pa.float32(), list_size=dim)), # LanceDB conventionally expects 'vector' for similarity search
            pa.field("size", pa.int32()),
            pa.field("representative_template", pa.string()),
            pa.field("representative_raw", pa.string()),
            pa.field("label", pa.string(), nullable=True),
            pa.field("is_acknowledged", pa.bool_()),
        ])

        data = []
        for c in valid_clusters:
            data.append({
                "cluster_id": c.cluster_id,
                "vector": c.centroid.astype(np.float32).tolist(),
                "size": c.size,
                "representative_template": c.representative_template,
                "representative_raw": c.representative_raw,
                "label": c.label,
                "is_acknowledged": c.is_acknowledged,
            })

        if not data:
            logger.warning("No valid clusters to store in baseline (only noise found).")
            # Still create an empty table
            db.create_table("centroids", schema=dynamic_schema)
        else:
            db.create_table("centroids", data=data, schema=dynamic_schema)

        # Write metadata
        metadata = BaselineMetadata(
            version="1.0",
            created_at=datetime.now(timezone.utc).isoformat(),
            embedding_model=settings.embedding_model,
            cluster_count=len(valid_clusters),
            total_logs_processed=total_logs,
            config_snapshot={
                "min_cluster_size": settings.min_cluster_size,
                "min_samples": settings.min_samples,
                "mode": settings.mode.value,
            },
        )

        with self.metadata_path.open("w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(metadata), f, indent=2)

        logger.info("Baseline built successfully.")

    def get_metadata(self) -> BaselineMetadata:
        """Load and return the baseline metadata."""
        if not self.metadata_path.exists():
            raise BaselineError(f"Baseline metadata not found at {self.metadata_path}")

        try:
            with self.metadata_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return BaselineMetadata(**data)
        except Exception as e:
            raise BaselineError(f"Failed to read baseline metadata: {e}") from e

    def load(self) -> lancedb.table.Table:
        """Load the centroids table for querying.

        Returns
        -------
        lancedb.table.Table
            The LanceDB table object.
        """
        if not self.path.exists():
            raise BaselineError(f"Baseline index not found at {self.path}")

        try:
            db = self._get_db()
            self._table = db.open_table("centroids")
            return self._table
        except Exception as e:
            raise BaselineError(f"Failed to open baseline table: {e}") from e

    def search(self, query_vectors: np.ndarray, k: int = 1) -> list[list[dict[str, Any]]]:
        """Search the baseline for the k nearest neighbors for a batch of vectors.

        Parameters
        ----------
        query_vectors : np.ndarray
            2D array of query vectors.
        k : int
            Number of nearest neighbors to retrieve.

        Returns
        -------
        list[list[dict[str, Any]]]
            A list where each element corresponds to a query vector, containing a list
            of the k nearest baseline centroids and their distances.
        """
        table = self.load()
        results = []

        # LanceDB currently requires executing search per query vector
        for vector in query_vectors:
            # .to_pandas() / .to_dicts() based on lancedb version. .to_list() is safe.
            res = table.search(vector.astype(np.float32)).limit(k).to_list()
            results.append(res)

        return results

    def update_cluster_metadata(
        self,
        cluster_id: int,
        label: str | None = None,
        is_acknowledged: bool | None = None
    ) -> bool:
        """Update metadata for a specific cluster in the baseline.

        Parameters
        ----------
        cluster_id : int
            The ID of the cluster to update.
        label : str | None
            The new label to apply.
        is_acknowledged : bool | None
            The new acknowledgement status.

        Returns
        -------
        bool
            True if successful.
        """
        table = self.load()
        
        updates = {}
        if label is not None:
            updates["label"] = f"'{label}'"
        if is_acknowledged is not None:
            updates["is_acknowledged"] = str(is_acknowledged).lower()

        if not updates:
            return False

        try:
            # LanceDB update syntax
            table.update(where=f"cluster_id = {cluster_id}", values=updates)
            logger.info(f"Updated metadata for cluster {cluster_id} in {self.path}")
            return True
        except Exception as e:
            logger.error(f"Failed to update cluster {cluster_id}: {e}")
            return False
