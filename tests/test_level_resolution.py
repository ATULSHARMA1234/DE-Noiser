"""
Tests for resolving a log's severity.

Same failure mode as the source resolution: only "level" was read, so
OpenTelemetry's "severity_text", syslog's "severity", and ECS "log.level" all
became the INFO default. A real ERROR filed as INFO quietly corrupts the
severity histogram, incident ranking and SLO error-budget maths.

The canonical vocabulary is the one the platform already uses — DEBUG, INFO,
WARN, ERROR, FATAL — because the Explore histogram hardcodes its colours and
stack order on exactly those names.
"""

import pytest

from denoiser.storage.clickhouse_store import DEFAULT_LEVEL, resolve_level

CANONICAL = {"DEBUG", "INFO", "WARN", "ERROR", "FATAL"}


class TestKeys:
    @pytest.mark.parametrize(
        "log,expected",
        [
            ({"level": "ERROR"}, "ERROR"),
            ({"severity_text": "ERROR"}, "ERROR"),   # OpenTelemetry
            ({"severity": "ERROR"}, "ERROR"),        # syslog-derived
            ({"log": {"level": "ERROR"}}, "ERROR"),  # ECS nested
            ({"loglevel": "ERROR"}, "ERROR"),
            ({"levelname": "ERROR"}, "ERROR"),       # Python logging
        ],
    )
    def test_each_supported_key(self, log, expected):
        assert resolve_level(log) == expected

    def test_explicit_level_wins_over_severity(self):
        assert resolve_level({"level": "ERROR", "severity": "DEBUG"}) == "ERROR"


class TestCanonicalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("WARNING", "WARN"),
            ("warn", "WARN"),
            ("Err", "ERROR"),
            ("CRITICAL", "FATAL"),
            ("crit", "FATAL"),
            ("fatal", "FATAL"),
            ("EMERGENCY", "FATAL"),
            ("panic", "FATAL"),
            ("trace", "DEBUG"),
            ("notice", "INFO"),
        ],
    )
    def test_aliases_map_to_platform_vocabulary(self, raw, expected):
        assert resolve_level({"level": raw}) == expected
        assert expected in CANONICAL

    def test_case_is_normalised(self):
        assert resolve_level({"level": "error"}) == "ERROR"

    def test_whitespace_is_trimmed(self):
        assert resolve_level({"level": "  warn  "}) == "WARN"


class TestSyslogNumeric:
    @pytest.mark.parametrize(
        "sev,expected",
        [(0, "FATAL"), (2, "FATAL"), (3, "ERROR"), (4, "WARN"), (6, "INFO"), (7, "DEBUG")],
    )
    def test_numeric_severity_int(self, sev, expected):
        assert resolve_level({"severity": sev}) == expected

    def test_numeric_severity_as_string(self):
        """Shippers often send the number as a string."""
        assert resolve_level({"severity": "3"}) == "ERROR"


class TestFallback:
    def test_no_level_field_defaults_to_info(self):
        assert resolve_level({"message": "hello"}) == DEFAULT_LEVEL

    def test_empty_string_falls_through(self):
        assert resolve_level({"level": "   ", "severity": "ERROR"}) == "ERROR"

    def test_unknown_label_is_passed_through_uppercased(self):
        """An unrecognised label is kept rather than hidden as INFO, but uppercased."""
        assert resolve_level({"level": "verbose"}) == "VERBOSE"

    def test_structural_value_is_skipped(self):
        assert resolve_level({"level": {"x": 1}, "severity": "ERROR"}) == "ERROR"
