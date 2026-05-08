"""
Local embedding provider using SentenceTransformers.

Includes a SQLite-based persistent cache to avoid recomputing embeddings
for log templates that have been seen in previous runs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import polars as pl
from sentence_transformers import SentenceTransformer

from denoiser.config import settings
from denoiser.exceptions import EmbeddingError
from denoiser.logging import get_logger

logger = get_logger(__name__)


class EmbeddingCache:
    """A SQLite-based persistent cache for string -> vector mappings."""

    def __init__(self, cache_dir: Path) -> None:
        self.db_path = cache_dir / "embeddings.sqlite3"
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    text_hash TEXT PRIMARY KEY,
                    vector BLOB
                )
                """
            )

    def _hash_text(self, text: str) -> str:
        # Simple md5 hash for text templates to keep DB keys small and fixed-size
        import hashlib
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def get_many(self, texts: list[str]) -> dict[str, np.ndarray]:
        """Fetch cached embeddings for a list of strings."""
        if not texts:
            return {}

        hashes = [self._hash_text(t) for t in texts]
        hash_to_text = dict(zip(hashes, texts))

        placeholders = ",".join("?" * len(hashes))
        query = f"SELECT text_hash, vector FROM embeddings WHERE text_hash IN ({placeholders})"

        results = {}
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, hashes)
            for text_hash, vector_blob in cursor:
                vector = np.frombuffer(vector_blob, dtype=np.float32)
                text = hash_to_text[text_hash]
                results[text] = vector

        return results

    def set_many(self, texts: list[str], vectors: np.ndarray) -> None:
        """Store computed embeddings in the cache."""
        if not texts or len(texts) != len(vectors):
            return

        records = [
            (self._hash_text(text), vector.astype(np.float32).tobytes())
            for text, vector in zip(texts, vectors)
        ]

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings (text_hash, vector) VALUES (?, ?)",
                records,
            )


class LocalEmbeddingProvider:
    """Generates dense vector embeddings using local SentenceTransformer models."""

    def __init__(self) -> None:
        self.model_name = settings.embedding_model
        self.batch_size = settings.embedding_batch_size
        self.dimension = settings.embedding_dimension
        
        # We lazy-load the model to keep CLI startup fast for non-embedding commands
        self._model: SentenceTransformer | None = None
        
        cache_dir = settings.ensure_cache_dir()
        self.cache = EmbeddingCache(cache_dir)

    def _load_model(self) -> None:
        if self._model is None:
            logger.info("Loading embedding model", extra={"model": self.model_name})
            try:
                # Disabling progress bars in internal huggingface/sentence_transformers calls
                # to prevent console spam
                self._model = SentenceTransformer(self.model_name)
            except Exception as e:
                raise EmbeddingError(f"Failed to load model {self.model_name}: {e}") from e

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed a list of text templates into vectors.

        Automatically uses the local SQLite cache to skip recomputing known templates.

        Parameters
        ----------
        texts : list[str]
            List of normalized log templates.

        Returns
        -------
        np.ndarray
            A 2D numpy array of shape (len(texts), dimension).
        """
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        logger.debug("Embedding batch", extra={"count": len(texts)})

        # 1. Check cache
        cached = self.cache.get_many(texts)
        
        # 2. Identify missing texts
        missing_texts = [t for t in texts if t not in cached]
        
        # 3. Compute missing
        if missing_texts:
            self._load_model()
            assert self._model is not None
            
            logger.info("Computing embeddings for unseen templates", extra={"count": len(missing_texts)})
            try:
                computed_vectors = self._model.encode(
                    missing_texts,
                    batch_size=self.batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
            except Exception as e:
                raise EmbeddingError(f"Failed to compute embeddings: {e}") from e
                
            if not isinstance(computed_vectors, np.ndarray):
                computed_vectors = np.array(computed_vectors)
                
            self.cache.set_many(missing_texts, computed_vectors)
            
            # Merge computed back into our working dict
            for text, vector in zip(missing_texts, computed_vectors):
                cached[text] = vector

        # 4. Reconstruct original order
        final_vectors = []
        for text in texts:
            vec = cached.get(text)
            if vec is None:
                raise EmbeddingError(f"Missing embedding for template: {text}")
            final_vectors.append(vec)

        return np.vstack(final_vectors)
