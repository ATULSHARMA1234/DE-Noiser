"""
Deduplication engine to group identical normalized messages.

This significantly reduces the number of calls to the embedding model by
only embedding unique semantic templates.
"""

from __future__ import annotations

from collections import defaultdict

from denoiser.ingestion.models import LogRecord
from denoiser.logging import get_logger

logger = get_logger(__name__)


class Deduplicator:
    """Groups LogRecords by their normalized text.

    Maintains a mapping of `normalized_text` -> `list[LogRecord]`.
    """

    def __init__(self) -> None:
        self._grouped_records: dict[str, list[LogRecord]] = defaultdict(list)
        self._template_counts: dict[str, int] = defaultdict(int)
        self._unique_count = 0
        self._total_count = 0

    def add(self, record: LogRecord) -> None:
        """Add a record to the deduplication pool.

        The record must have its `normalized_text` field populated.

        Parameters
        ----------
        record : LogRecord
            The normalized log record.
        """
        if not record.normalized_text:
            logger.warning(
                "Record added without normalized_text. Grouping by raw_text instead.",
                extra={"line_number": record.line_number, "source": record.source},
            )
            key = record.raw_text
        else:
            key = record.normalized_text

        if key not in self._grouped_records:
            self._unique_count += 1
            
        # Optimization: only store first N records to save massive memory in 1M+ log tests
        if len(self._grouped_records[key]) < 100:
            self._grouped_records[key].append(record)
            
        self._total_count += 1
        self._template_counts[key] += 1

    def get_unique_templates(self) -> list[str]:
        """Return the list of unique normalized templates."""
        return list(self._grouped_records.keys())

    def get_records_for_template(self, template: str) -> list[LogRecord]:
        """Return all records that share the given normalized template."""
        return self._grouped_records.get(template, [])

    def get_all_groups(self) -> dict[str, list[LogRecord]]:
        """Return the underlying mapping of template -> sample records."""
        return dict(self._grouped_records)

    def get_all_counts(self) -> dict[str, int]:
        """Return the underlying mapping of template -> total occurrences."""
        return dict(self._template_counts)

    @property
    def total_count(self) -> int:
        """Total number of records processed."""
        return self._total_count

    @property
    def unique_count(self) -> int:
        """Number of unique semantic templates."""
        return self._unique_count

    def clear(self) -> None:
        """Clear all stored records and reset counts."""
        self._grouped_records.clear()
        self._unique_count = 0
        self._total_count = 0
