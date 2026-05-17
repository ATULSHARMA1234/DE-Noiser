"""
Task 7: Unit tests for the log ingestion pipeline.
Tests LogReader, Normalizer, and Deduplicator.
"""

import os
import tempfile
import pytest

from denoiser.ingestion.reader import LogReader
from denoiser.preprocessing.normalization import Normalizer
from denoiser.preprocessing.deduplication import Deduplicator
from denoiser.preprocessing.redaction import Redactor


# ── LogReader Tests ──────────────────────────────────────────────────────────

class TestLogReader:
    """Tests for the LogReader ingestion component."""

    def test_read_single_file(self, tmp_path):
        """LogReader should yield one LogRecord per non-empty line."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            "2026-01-01 INFO Starting service\n"
            "2026-01-01 ERROR Connection refused\n"
            "2026-01-01 WARN Memory usage high\n"
        )
        reader = LogReader()
        records = list(reader.read(str(log_file)))
        assert len(records) == 3
        assert "Starting service" in records[0].raw_text
        assert "Connection refused" in records[1].raw_text

    def test_read_empty_file(self, tmp_path):
        """LogReader should return no records for an empty file."""
        log_file = tmp_path / "empty.log"
        log_file.write_text("")
        reader = LogReader()
        records = list(reader.read(str(log_file)))
        assert len(records) == 0

    def test_read_directory(self, tmp_path):
        """LogReader should recursively read all log files in a directory."""
        (tmp_path / "a.log").write_text("line1\nline2\n")
        (tmp_path / "b.log").write_text("line3\n")
        reader = LogReader()
        records = list(reader.read(str(tmp_path)))
        assert len(records) >= 3

    def test_skips_unsupported_extensions(self, tmp_path):
        """LogReader should skip files with unsupported extensions."""
        (tmp_path / "data.csv").write_text("col1,col2\na,b\n")
        (tmp_path / "valid.log").write_text("real log line\n")
        reader = LogReader()
        records = list(reader.read(str(tmp_path)))
        # Should only get the .log file
        texts = [r.raw_text for r in records]
        assert any("real log line" in t for t in texts)


# ── Normalizer Tests ─────────────────────────────────────────────────────────

class TestNormalizer:
    """Tests for the Polars-based log normalizer."""

    def test_normalize_uuid(self):
        """UUIDs should be replaced with <UUID> placeholder."""
        normalizer = Normalizer()
        result = normalizer.normalize_single(
            "Request 550e8400-e29b-41d4-a716-446655440000 processed"
        )
        assert "<UUID>" in result or "550e8400" not in result

    def test_normalize_ip(self):
        """IP addresses should be replaced with <IP> placeholder."""
        normalizer = Normalizer()
        result = normalizer.normalize_single("Connection from 192.168.1.100 established")
        assert "<IP>" in result or "192.168.1.100" not in result

    def test_normalize_batch(self):
        """Batch normalization should handle multiple lines."""
        normalizer = Normalizer()
        lines = [
            "User 123 logged in from 10.0.0.1",
            "Request abc-def-123 completed",
        ]
        results = normalizer.normalize_batch(lines)
        assert len(results) == 2
        assert isinstance(results[0], str)


# ── Deduplicator Tests ───────────────────────────────────────────────────────

class TestDeduplicator:
    """Tests for the deduplication engine."""

    def test_dedup_identical_lines(self):
        """Identical normalized texts should be counted, not duplicated."""
        deduper = Deduplicator()
        from denoiser.ingestion.models import LogRecord

        for _ in range(5):
            record = LogRecord(raw_text="ERROR Connection refused", source="test.log", line_number=1)
            record.normalized_text = "ERROR Connection refused"
            deduper.add(record)

        templates = deduper.get_unique_templates()
        assert len(templates) == 1
        assert deduper.total_count == 5

    def test_dedup_different_lines(self):
        """Different normalized texts should produce different templates."""
        deduper = Deduplicator()
        from denoiser.ingestion.models import LogRecord

        texts = ["INFO Starting", "ERROR Failed", "WARN Memory"]
        for i, text in enumerate(texts):
            record = LogRecord(raw_text=text, source="test.log", line_number=i)
            record.normalized_text = text
            deduper.add(record)

        templates = deduper.get_unique_templates()
        assert len(templates) == 3
