"""
Universal timestamp extractor module.

Task 9: Parses ISO 8601, Unix epoch, syslog, AWS CloudWatch, and Docker compose
timestamp formats into UTC epoch milliseconds.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

from denoiser.logging import get_logger

logger = get_logger(__name__)


class TimestampExtractor:
    """Universal log timestamp extractor.

    Parses various standard log timestamp formats into UTC epoch milliseconds.
    Supported formats:
    - ISO 8601 / RFC 3339 (e.g. 2026-05-22T23:25:51.123Z, 2026-05-22 23:25:51.123)
    - Unix Epoch (seconds and milliseconds, e.g. 1715934500, 1715934500000)
    - Syslog RFC 3164 (e.g. May 22 23:25:51)
    - AWS CloudWatch (e.g. starting with 13-digit epoch ms)
    - Docker Compose log format (strips container prefix and extracts timestamp)
    """

    MONTHS = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
    }

    def __init__(self) -> None:
        # 1. Docker Compose prefix pattern (e.g., "auth-service-1  | " or "api-1|")
        self.docker_prefix_re = re.compile(r"^\s*([a-zA-Z0-9_-]+(?:-\d+)?\s*\|)\s*")

        # 2. 13-digit Unix Epoch Milliseconds
        self.epoch_ms_re = re.compile(r"(?:\b|^\s*)(\d{13})\b")

        # 3. 10-digit Unix Epoch Seconds with optional fractions
        self.epoch_sec_re = re.compile(r"(?:\b|^\s*)(\d{10})(?:\.(\d{1,6}))?\b")

        # 4. ISO 8601 / RFC 3339
        # Group 1: Year, 2: Month, 3: Day, 4: Hour, 5: Minute, 6: Second, 7: Fraction, 8: Timezone
        self.iso8601_re = re.compile(
            r"\b(\d{4})[-/](\d{2})[-/](\d{2})[T\s](\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,6}))?\s*(Z|[+-]\d{2}:?\d{2})?\b"
        )

        # 5. Syslog (RFC 3164)
        # Group 1: Month, 2: Day, 3: Hour, 4: Minute, 5: Second
        self.syslog_re = re.compile(
            r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})\b"
        )

    def extract(self, text: str) -> int | None:
        """Extract timestamp from log text and convert to UTC epoch milliseconds.

        Parameters
        ----------
        text : str
            The raw log line.

        Returns
        -------
        int | None
            Epoch milliseconds in UTC, or None if no valid timestamp is found.
        """
        if not text:
            return None

        # Step 1: Strip Docker Compose prefix if present to look at clean log start
        docker_match = self.docker_prefix_re.match(text)
        if docker_match:
            text = text[docker_match.end():]

        # Step 2: Try 13-digit Millisecond Epoch
        epoch_ms_match = self.epoch_ms_re.search(text)
        if epoch_ms_match and epoch_ms_match.start() < 50:
            val = int(epoch_ms_match.group(1))
            # Validate range: Year 2000 to Year 2100 in epoch milliseconds
            if 946684800000 <= val <= 4102444800000:
                return val

        # Step 3: Try 10-digit Second Epoch (with optional decimals)
        epoch_sec_match = self.epoch_sec_re.search(text)
        if epoch_sec_match and epoch_sec_match.start() < 50:
            sec_val = int(epoch_sec_match.group(1))
            # Validate range: Year 2000 to Year 2100 in epoch seconds
            if 946684800 <= sec_val <= 4102444800:
                frac_str = epoch_sec_match.group(2)
                ms_val = self._frac_to_ms(frac_str)
                return sec_val * 1000 + ms_val

        # Step 4: Try ISO 8601
        iso_match = self.iso8601_re.search(text)
        if iso_match:
            try:
                year = int(iso_match.group(1))
                month = int(iso_match.group(2))
                day = int(iso_match.group(3))
                hour = int(iso_match.group(4))
                minute = int(iso_match.group(5))
                second = int(iso_match.group(6))

                frac_str = iso_match.group(7)
                ms_val = self._frac_to_ms(frac_str)

                tz_str = iso_match.group(8)
                tz = UTC
                if tz_str and tz_str != "Z":
                    tz_str = tz_str.replace(":", "")  # normalizes +05:30 to +0530
                    sign = 1 if tz_str[0] == "+" else -1
                    hours_offset = int(tz_str[1:3])
                    mins_offset = int(tz_str[3:5]) if len(tz_str) > 3 else 0
                    tz = timezone(sign * timedelta(hours=hours_offset, minutes=mins_offset))

                dt = datetime(year, month, day, hour, minute, second, microsecond=ms_val * 1000, tzinfo=tz)
                return int(dt.timestamp() * 1000)
            except Exception as e:
                logger.debug("Failed parsing match as ISO 8601", extra={"error": str(e)})

        # Step 5: Try Syslog RFC 3164
        syslog_match = self.syslog_re.search(text)
        if syslog_match:
            try:
                month_str = syslog_match.group(1)
                month = self.MONTHS[month_str]
                day = int(syslog_match.group(2))
                hour = int(syslog_match.group(3))
                minute = int(syslog_match.group(4))
                second = int(syslog_match.group(5))

                # Default to current year in UTC
                now = datetime.now(UTC)
                year = now.year

                dt = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
                if dt > now + timedelta(days=7):
                    # Future date, likely from last year
                    dt = dt.replace(year=year - 1)

                return int(dt.timestamp() * 1000)
            except Exception as e:
                logger.debug("Failed parsing match as Syslog", extra={"error": str(e)})

        return None

    def _frac_to_ms(self, frac_str: str | None) -> int:
        """Convert fractional seconds string to integer milliseconds."""
        if not frac_str:
            return 0
        frac_str = frac_str.ljust(3, '0') if len(frac_str) < 3 else frac_str[:3]
        return int(frac_str)
