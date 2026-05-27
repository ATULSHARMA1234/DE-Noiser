"""
Structured logging for Semantic Log De-Noiser.

Provides a Rich-powered console logger for beautiful terminal output and a
privacy-aware filter that prevents accidental secret leakage into log messages.

Usage::

    from denoiser.logging import get_logger
    log = get_logger(__name__)
    log.info("Processing %d log lines", count)
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from rich.console import Console
from rich.logging import RichHandler

from denoiser.api.middleware import request_id_ctx

if TYPE_CHECKING:
    from denoiser.config import LogLevel

console = Console(stderr=True)

_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9\-._~+/]+=*"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(password\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(secret\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(token\s*[=:]\s*)[^\s,;\"']+"),
]


class PrivacyFilter(logging.Filter):
    """Scrub potential secrets from log records before they reach any handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern in _SENSITIVE_PATTERNS:
            msg = pattern.sub(r"\g<1><REDACTED>", msg)
        record.msg = msg
        record.args = None
        return True


class RequestIdPrefixFilter(logging.Filter):
    """Prefix log messages with the active request_id (if present)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rid = request_id_ctx.get("no-request")
        except Exception:
            return True

        if not rid or rid == "no-request":
            return True

        # At this point PrivacyFilter already sanitized record.msg into a string.
        # Ensure we don't double-prefix.
        msg = record.msg if isinstance(record.msg, str) else record.getMessage()
        prefix = f"[{rid}] "
        if isinstance(msg, str) and not msg.startswith(prefix):
            record.msg = prefix + msg
            record.args = None
        return True


_CONFIGURED = False


def setup_logging(level: LogLevel | str = "INFO") -> None:
    """Configure the root denoiser logger with Rich output. Safe to call multiple times."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_str = level.value if hasattr(level, "value") else str(level).upper()

    handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        rich_tracebacks=True,
        tracebacks_show_locals=level_str == "DEBUG",
        markup=True,
    )
    handler.addFilter(PrivacyFilter())
    handler.addFilter(RequestIdPrefixFilter())

    root_logger = logging.getLogger("denoiser")
    root_logger.setLevel(level_str)
    root_logger.addHandler(handler)
    root_logger.propagate = False

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the denoiser namespace."""
    from denoiser.config import settings
    setup_logging(settings.log_level)

    if not name.startswith("denoiser"):
        name = f"denoiser.{name}"
    return logging.getLogger(name)
