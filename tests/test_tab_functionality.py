"""Backing behaviour for the Explore / Metrics / Traces tabs.

Each of these tabs queries the ClickHouse store, and nothing ever wrote analysed
log files into it: a source you had just analysed returned nothing in Explore,
produced no extracted metrics, and could not be monitored. Traces had no path in
at all short of a live OTLP exporter.
"""

import json

import pytest

from denoiser.storage.clickhouse_store import DEFAULT_LEVEL, resolve_level
from denoiser.utils.time import iso_utc, to_epoch_ms, utcnow


class TestLevelFromMessage:
    """Plain-text logs carry severity in the line, not in a field.

    Every such line was indexed as INFO, so `level:ERROR` in Explore matched
    nothing in a file where every line said ERROR.
    """

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("2024-11-29T10:00:00Z [vendor-payment-0] ERROR Payment reconciliation failed", "ERROR"),
            ("node-api-1 | 2026-05-17 [warn]: Redis cache miss", "WARN"),
            ("[info] listening on port 3000", "INFO"),
            ("CRITICAL: disk full", "FATAL"),
            ("FATAL unrecoverable state", "FATAL"),
            ("WARNING: retrying", "WARN"),
        ],
    )
    def test_severity_is_read_from_the_line(self, message, expected):
        assert resolve_level({"message": message}) == expected

    @pytest.mark.parametrize(
        "message",
        [
            "no errors found in the report",       # prose, not a level
            "GET /api/orders 200 OK",              # OK is not a severity
            "user warned about quota",             # inflected word
            "hello",
        ],
    )
    def test_prose_is_not_mistaken_for_a_level(self, message):
        assert resolve_level({"message": message}) == DEFAULT_LEVEL

    def test_an_explicit_field_still_wins(self):
        assert resolve_level({"level": "debug", "message": "ERROR in text"}) == "DEBUG"

    def test_raw_text_is_read_when_there_is_no_message(self):
        assert resolve_level({"raw_text": "ERROR boom"}) == "ERROR"


class TestSearchIndexing:
    """The analysis worker must hand its records to the searchable store."""

    @staticmethod
    def _records():
        now = utcnow()
        return [
            {
                "raw_text": "2024-11-29T10:00:00Z ERROR Payment failed",
                "source_path": "data/payments.log",
                "source_label": "payments",
                "line_number": 1,
                "timestamp": now,
                "timestamp_ms": to_epoch_ms(now),
                "metadata": json.dumps({"service": "payment-api"}),
            },
            {
                "raw_text": "2024-11-29T10:00:01Z INFO Payment retried",
                "source_path": "data/payments.log",
                "source_label": "payments",
                "line_number": 2,
                "timestamp": now,
                "timestamp_ms": to_epoch_ms(now),
                "metadata": None,
            },
        ]

    def test_records_are_indexed_with_service_level_and_run(self, monkeypatch):
        from denoiser.workers import analysis_worker

        captured = {}

        class FakeStore:
            client = object()

            def insert_logs(self, logs, tenant_id):
                captured["logs"] = logs
                captured["tenant_id"] = tenant_id
                return True

        monkeypatch.setattr("denoiser.storage.clickhouse_store.ClickHouseStore", lambda: FakeStore())

        indexed = analysis_worker.index_records_for_search(self._records(), tenant_id=1, run_id="run-abc")

        assert indexed == 2
        assert captured["tenant_id"] == "1"
        first, second = captured["logs"]
        assert first["service"] == "payment-api"        # from the record metadata
        assert second["service"] == "payments"          # falls back to the file
        assert all(entry["run_id"] == "run-abc" for entry in captured["logs"])
        assert resolve_level(first) == "ERROR"

    def test_missing_tenant_is_not_indexed(self, monkeypatch):
        """Unscoped rows are refused by the store; don't even offer them."""
        from denoiser.workers import analysis_worker

        assert analysis_worker.index_records_for_search(self._records(), tenant_id=None, run_id="r") == 0

    def test_store_being_down_is_not_fatal(self, monkeypatch):
        from denoiser.workers import analysis_worker

        class DeadStore:
            client = None

        monkeypatch.setattr("denoiser.storage.clickhouse_store.ClickHouseStore", lambda: DeadStore())
        assert analysis_worker.index_records_for_search(self._records(), tenant_id=1, run_id="r") == 0


class TestNaiveTimestampSerialisation:
    """Naive UTC sent without a zone is read by browsers as local time."""

    def test_iso_utc_marks_the_offset(self):
        stamped = iso_utc(utcnow())
        assert stamped.endswith("+00:00")

    def test_iso_utc_passes_through_none(self):
        assert iso_utc(None) is None

    def test_epoch_ms_reads_naive_as_utc(self):
        import datetime

        naive = datetime.datetime(2026, 7, 25, 22, 0, 0)
        aware = naive.replace(tzinfo=datetime.UTC)
        assert to_epoch_ms(naive) == to_epoch_ms(aware) == int(aware.timestamp() * 1000)
