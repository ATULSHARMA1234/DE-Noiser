"""
Data models for log ingestion and processing.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any


@dataclasses.dataclass(slots=True)
class LogRecord:
    """A structured representation of a single log event.

    Attributes
    ----------
    raw_text : str
        The original, unmodified log line.
    timestamp : datetime | None
        The parsed timestamp of the event, if available.
    source : str
        The origin of the log (e.g., file path, 'stdin').
    line_number : int
        The line number within the source.
    metadata : dict[str, Any]
        Any additional structured data extracted during parsing (e.g., from JSON logs).
    normalized_text : str
        The text after dynamic tokens (UUIDs, IPs, etc.) have been redacted/normalized.
        Populated during the preprocessing phase.
    """

    raw_text: str
    source: str
    line_number: int
    timestamp: datetime | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    normalized_text: str = ""
    org_id: str | None = None
    team_id: str | None = None
