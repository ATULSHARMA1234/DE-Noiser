"""
Unit tests for the Universal Timestamp Extractor.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from denoiser.preprocessing.timestamp import TimestampExtractor


class TestTimestampExtractor:
    """Tests for the universal log timestamp extractor."""

    @pytest.fixture
    def extractor(self):
        return TimestampExtractor()

    def test_extract_iso8601_utc(self, extractor):
        """ISO 8601 timestamps in UTC should be correctly parsed."""
        log = "2026-05-22T23:25:51.123Z [INFO] Application booted"
        expected = int(datetime(2026, 5, 22, 23, 25, 51, microsecond=123000, tzinfo=timezone.utc).timestamp() * 1000)
        assert extractor.extract(log) == expected

    def test_extract_iso8601_no_tz(self, extractor):
        """ISO 8601 naive timestamps should be assumed as UTC."""
        log = "2026-05-22 23:25:51.123 [INFO] Database connected"
        expected = int(datetime(2026, 5, 22, 23, 25, 51, microsecond=123000, tzinfo=timezone.utc).timestamp() * 1000)
        assert extractor.extract(log) == expected

    def test_extract_iso8601_slash_and_comma(self, extractor):
        """ISO 8601 variants (slashes, commas) should be successfully parsed."""
        log = "2026/05/22 23:25:51,456 [INFO] Job completed"
        expected = int(datetime(2026, 5, 22, 23, 25, 51, microsecond=456000, tzinfo=timezone.utc).timestamp() * 1000)
        assert extractor.extract(log) == expected

    def test_extract_iso8601_timezone_offset(self, extractor):
        """ISO 8601 offsets should be adjusted to UTC epoch milliseconds."""
        log = "2026-05-22T23:25:51.123+05:30 [DEBUG] Health check"
        # +05:30 means the local time is 23:25:51, so UTC is 5h 30m earlier (17:55:51)
        expected = int(datetime(2026, 5, 22, 17, 55, 51, microsecond=123000, tzinfo=timezone.utc).timestamp() * 1000)
        assert extractor.extract(log) == expected

    def test_extract_epoch_seconds(self, extractor):
        """Unix epoch seconds (10 digits) with decimal fraction should be parsed."""
        log = "1715934500.123 Connection accepted from client"
        expected = 1715934500123
        assert extractor.extract(log) == expected

    def test_extract_epoch_seconds_no_decimal(self, extractor):
        """Unix epoch seconds (10 digits) without decimals should be parsed."""
        log = "1715934500 Starting worker background service"
        expected = 1715934500000
        assert extractor.extract(log) == expected

    def test_extract_epoch_milliseconds(self, extractor):
        """Unix epoch milliseconds (13 digits) should be parsed."""
        log = "1715934500123\t[INFO]\tRequest processed"
        expected = 1715934500123
        assert extractor.extract(log) == expected

    def test_extract_syslog_basic(self, extractor):
        """Syslog format should be parsed using current year."""
        log = "May 22 23:25:51 hostname cron[12345]: log message"
        year = datetime.now(timezone.utc).year
        expected = int(datetime(year, 5, 22, 23, 25, 51, tzinfo=timezone.utc).timestamp() * 1000)
        assert extractor.extract(log) == expected

    def test_extract_syslog_single_digit_day(self, extractor):
        """Syslog formatting with multiple spaces for single-digit day should be parsed."""
        log = "May  2 23:25:51 hostname service[123]: signal handled"
        year = datetime.now(timezone.utc).year
        expected = int(datetime(year, 5, 2, 23, 25, 51, tzinfo=timezone.utc).timestamp() * 1000)
        assert extractor.extract(log) == expected

    def test_extract_syslog_year_fallback(self, extractor):
        """If syslog date is in the future, it should fall back to previous year."""
        # Force a date that is clearly in the future relative to UTC 'now'
        # e.g., if now is May 2026, Dec 22 should fall back to Dec 2025.
        now = datetime.now(timezone.utc)
        future_time = now + timedelta(days=15)
        month_str = future_time.strftime("%b")
        day = future_time.day
        
        log = f"{month_str} {day:02d} 12:00:00 service[123]: signal"
        parsed = extractor.extract(log)
        
        parsed_dt = datetime.fromtimestamp(parsed / 1000, timezone.utc)
        assert parsed_dt.year == now.year - 1

    def test_extract_docker_compose_prefix(self, extractor):
        """Docker Compose container prefix should be stripped before parsing."""
        log = "auth-service-1  | 2026-05-22T23:25:51.123Z [INFO] Validation succeeded"
        expected = int(datetime(2026, 5, 22, 23, 25, 51, microsecond=123000, tzinfo=timezone.utc).timestamp() * 1000)
        assert extractor.extract(log) == expected

    def test_extract_no_timestamp(self, extractor):
        """Lines without recognizable timestamps should return None."""
        assert extractor.extract("Application failed to start unexpectedly") is None
        assert extractor.extract("") is None
