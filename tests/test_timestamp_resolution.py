"""
Tests for resolving a log's event time in the ClickHouse write path.

Two failure modes, both found by reasoning about real shipper data:

  1. Only "timestamp" was read, so Elastic's "@timestamp", Docker's "time",
     OTel's "timeUnixNano" and Fluent Bit's "date" fell back to wall-clock,
     detaching stored time from the event.

  2. Epoch was assumed to be seconds. A millisecond epoch (JavaScript Date.now,
     countless loggers) raised "year 56531 is out of range" inside the batch
     loop, failing the whole batch. In the at-least-once worker that batch is a
     poison pill that permanently wedges the partition.

This is distinct from preprocessing.TimestampExtractor, which scrapes a time
out of unstructured log *text*; here we coerce a structured field value.
"""

from datetime import UTC, datetime

import pytest

from denoiser.storage.clickhouse_store import (
    coerce_timestamp,
    resolve_timestamp,
)

# 2024-07-24T05:46:40Z in each unit.
EPOCH_S = 1721800000
EPOCH_MS = 1721800000000
EPOCH_US = 1721800000000000
EPOCH_NS = 1721800000000000000
EXPECTED = datetime(2024, 7, 24, 5, 46, 40, tzinfo=UTC)


class TestEpochUnits:
    @pytest.mark.parametrize("value", [EPOCH_S, EPOCH_MS, EPOCH_US, EPOCH_NS])
    def test_every_unit_resolves_to_the_same_instant(self, value):
        """A millisecond epoch used to crash; all four units must now agree."""
        assert coerce_timestamp(value) == EXPECTED

    def test_millisecond_epoch_does_not_raise(self):
        """The exact poison-pill trigger: JavaScript Date.now()."""
        assert coerce_timestamp(1721800000000) is not None

    def test_float_seconds_keep_subsecond(self):
        dt = coerce_timestamp(1721800000.5)
        assert dt is not None
        assert dt.microsecond == 500000

    def test_numeric_string_epoch(self):
        assert coerce_timestamp("1721800000000") == EXPECTED


class TestIso:
    def test_iso_with_zulu(self):
        assert coerce_timestamp("2024-07-24T05:46:40Z") == EXPECTED

    def test_iso_with_offset(self):
        # +05:30 of the same wall time is 05:30 earlier in UTC.
        dt = coerce_timestamp("2024-07-24T11:16:40+05:30")
        assert dt == EXPECTED

    def test_naive_iso_is_treated_as_utc(self):
        dt = coerce_timestamp("2024-07-24T05:46:40")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt == EXPECTED


class TestUnparseable:
    @pytest.mark.parametrize("value", [None, "", "   ", "not-a-date", True, False, {"x": 1}, ["a"]])
    def test_returns_none_rather_than_raising(self, value):
        assert coerce_timestamp(value) is None


class TestKeyResolution:
    @pytest.mark.parametrize(
        "log",
        [
            {"timestamp": EPOCH_MS},
            {"@timestamp": "2024-07-24T05:46:40Z"},   # Elastic Common Schema
            {"time": "2024-07-24T05:46:40Z"},          # Docker json-file driver
            {"ts": EPOCH_MS},
            {"timeUnixNano": EPOCH_NS},                # OpenTelemetry, mixed case
            {"date": EPOCH_S},                         # Fluent Bit
        ],
    )
    def test_supported_keys_resolve(self, log):
        assert resolve_timestamp(log) == EXPECTED

    def test_explicit_timestamp_wins(self):
        log = {"timestamp": EPOCH_MS, "@timestamp": "1999-01-01T00:00:00Z"}
        assert resolve_timestamp(log) == EXPECTED

    def test_no_timestamp_falls_back_to_now(self):
        before = datetime.now(UTC)
        resolved = resolve_timestamp({"message": "no time here"})
        after = datetime.now(UTC)
        assert before <= resolved <= after

    def test_unparseable_value_falls_through_to_next_key(self):
        """A junk primary key must not shadow a good secondary one."""
        log = {"timestamp": "garbage", "@timestamp": "2024-07-24T05:46:40Z"}
        assert resolve_timestamp(log) == EXPECTED

    def test_unparseable_everywhere_falls_back_to_now(self):
        before = datetime.now(UTC)
        resolved = resolve_timestamp({"timestamp": "garbage", "time": "also-garbage"})
        after = datetime.now(UTC)
        assert before <= resolved <= after
