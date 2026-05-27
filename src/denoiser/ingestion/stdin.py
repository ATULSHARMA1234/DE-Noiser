"""
Log ingestion module for reading from standard input (stdin).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator

from denoiser.config import settings
from denoiser.ingestion.models import LogRecord
from denoiser.logging import get_logger

logger = get_logger(__name__)


class StdinReader:
    """Reads log lines from standard input (stdin) and yields LogRecords.

    Designed for pipeline usage, e.g., `kubectl logs | semantic-log analyze -`
    """

    def __init__(self) -> None:
        self.max_line_length = settings.max_line_length

    def read(self) -> Iterator[LogRecord]:
        """Yield LogRecords from stdin.

        Yields
        ------
        LogRecord
            A structured log record for each line processed.
        """
        if sys.stdin.isatty():
            logger.warning("Reading from stdin, but it is attached to a TTY. Waiting for input...")

        logger.debug("Starting stdin read")

        try:
            for line_idx, line in enumerate(sys.stdin, start=1):
                raw_text = line.strip()
                if not raw_text:
                    continue

                if len(raw_text) > self.max_line_length:
                    logger.debug(
                        "Truncating long line from stdin",
                        extra={
                            "line": line_idx,
                            "length": len(raw_text),
                        },
                    )
                    raw_text = raw_text[: self.max_line_length]

                metadata = {}
                # Attempt heuristic JSON parsing for stdin streams
                if raw_text.startswith("{") and raw_text.endswith("}"):
                    try:
                        parsed = json.loads(raw_text)
                        if isinstance(parsed, dict):
                            metadata = parsed
                            raw_text = str(parsed.get("message", parsed.get("msg", raw_text)))
                    except json.JSONDecodeError:
                        pass

                yield LogRecord(
                    raw_text=raw_text,
                    source="stdin",
                    line_number=line_idx,
                    metadata=metadata,
                )
        except KeyboardInterrupt:
            logger.info("Stdin reading interrupted by user")
            return

        logger.debug("Finished stdin read", extra={"lines_read": line_idx if 'line_idx' in locals() else 0})
