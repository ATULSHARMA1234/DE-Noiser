"""
Normalization engine using Polars for high-performance text wrangling.

Replaces dynamic, high-cardinality tokens (UUIDs, IPs, timestamps, hex values)
with generic placeholders. This is critical for grouping semantically identical
log lines that differ only in request IDs or hostnames.
"""

from __future__ import annotations

import polars as pl

from denoiser.logging import get_logger

logger = get_logger(__name__)

# Patterns for normalization. The order matters less here than for redaction,
# but we still want to match larger structures (like timestamps) before smaller ones (like numbers).
_NORMALIZATION_RULES = {
    # UUIDs (standard 8-4-4-4-12 format)
    "UUID": r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",

    # IPv4 and IPv6 addresses
    "IP_ADDRESS": r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b|\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b",

    # ISO 8601 / RFC 3339 style timestamps (e.g., 2023-10-25T14:30:00.000Z)
    "TIMESTAMP": r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b",

    # Hexadecimal values (e.g., 0x1a2b, or long hex strings like Git hashes)
    # We want to match things that look like hashes or memory addresses, but avoid matching
    # regular words that happen to contain a-f.
    "HEX": r"(?i)\b0x[0-9a-f]+\b|\b[0-9a-f]{10,}\b",

    # Standalone numbers or numbers attached to word boundaries
    "NUMBER": r"\b\d+\b",

    # Catch-all for any sequence containing numbers (very aggressive for 1M log tests)
    "ID_NUMBER": r"\d+",
}


class Normalizer:
    """Normalizes log text using Polars."""

    def __init__(self) -> None:
        pass

    def normalize_batch(self, texts: list[str]) -> list[str]:
        """Normalize a batch of strings using Polars string replacement.

        Parameters
        ----------
        texts : list[str]
            A list of raw log strings.

        Returns
        -------
        list[str]
            A list of normalized log strings.
        """
        if not texts:
            return []

        df = pl.DataFrame({"text": texts})
        expr = pl.col("text")

        # Chain replace_all operations for each pattern
        for label, pattern in _NORMALIZATION_RULES.items():
            expr = expr.str.replace_all(pattern, f"<{label}>")

        # Reduce multiple spaces to a single space
        expr = expr.str.replace_all(r"\s+", " ")
        # Strip leading/trailing whitespace
        expr = expr.str.strip_chars()

        normalized_df = df.select(expr)
        return normalized_df["text"].to_list()

    def normalize_single(self, text: str) -> str:
        """Normalize a single string (convenience wrapper around normalize_batch)."""
        return self.normalize_batch([text])[0]
