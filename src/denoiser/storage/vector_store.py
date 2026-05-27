from pathlib import Path
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa

from denoiser.config import settings
from denoiser.logging import get_logger

logger = get_logger(__name__)

# Define the schema for the embeddings table
# We use pyarrow schema for exact control
schema = pa.schema([
    pa.field("id", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), settings.embedding_dimension)),
    pa.field("template", pa.string()),
    pa.field("source", pa.string()),
    pa.field("timestamp", pa.int64()),
])

class VectorStore:
    def __init__(self, uri: str = "data/lancedb"):
        self.uri = uri
        self.db = None
        self.table_name = "log_embeddings"
        self._init_db()

    def _init_db(self):
        try:
            # Ensure parent dir exists
            Path(self.uri).parent.mkdir(parents=True, exist_ok=True)
            self.db = lancedb.connect(self.uri)

            if self.table_name not in self.db.table_names():
                logger.info(f"Creating new LanceDB table '{self.table_name}' at {self.uri}")
                self.db.create_table(self.table_name, schema=schema)
        except Exception as e:
            logger.error(f"Failed to initialize LanceDB: {e}")

    def add_embeddings(self, ids: list[str], vectors: list[list[float]], templates: list[str], sources: list[str], timestamps: list[int]):
        """Persist new embeddings to the vector database"""
        if not self.db:
            return False

        try:
            table = self.db.open_table(self.table_name)
            vector_rows = np.asarray(vectors, dtype=np.float32).tolist()

            # Format data as a list of dictionaries matching the schema
            data = [
                {
                    "id": ids[i],
                    "vector": vector_rows[i],
                    "template": templates[i],
                    "source": sources[i] if i < len(sources) else "unknown",
                    "timestamp": timestamps[i] if i < len(timestamps) else 0
                }
                for i in range(min(len(ids), len(vector_rows), len(templates)))
            ]

            if not data:
                return True

            table.add(data)
            logger.info(f"Persisted {len(ids)} embeddings to LanceDB.")
            return True
        except Exception as e:
            logger.error(f"Failed to add embeddings to LanceDB: {e}")
            return False

    def search(self, query_vector: list[float], limit: int = 10) -> list[dict[str, Any]]:
        """Search the persistent vector database for semantically similar logs"""
        if not self.db:
            return []

        try:
            table = self.db.open_table(self.table_name)
            results = table.search(query_vector).limit(limit).to_list()
            return results
        except Exception as e:
            logger.error(f"Failed to search LanceDB: {e}")
            return []
