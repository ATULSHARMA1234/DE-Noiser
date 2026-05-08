"""
Log ingestion module for reading from files and directories.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from denoiser.config import settings
from denoiser.exceptions import IngestionError
from denoiser.ingestion.models import LogRecord
from denoiser.logging import get_logger

logger = get_logger(__name__)


class LogReader:
    """Reads log files and yields structured LogRecord objects.

    Supports reading individual files or recursively scanning directories.
    Handles basic text logs and JSONL/NDJSON formats.
    """

    def __init__(self) -> None:
        self.supported_extensions = set(settings.supported_extensions)
        self.max_line_length = settings.max_line_length

    def read(self, path: Path | str) -> Iterator[LogRecord]:
        """Yield LogRecords from the given path (file or directory).

        Parameters
        ----------
        path : Path | str
            The file or directory to read from.

        Yields
        ------
        LogRecord
            A structured log record for each line processed.

        Raises
        ------
        IngestionError
            If the path does not exist or cannot be accessed.
        """
        path_obj = Path(path).resolve()

        if not path_obj.exists():
            raise IngestionError(f"Path does not exist: {path_obj}", path=str(path_obj))

        if path_obj.is_dir():
            yield from self._read_directory(path_obj)
        else:
            yield from self._read_file(path_obj)

    def _read_directory(self, dir_path: Path) -> Iterator[LogRecord]:
        """Recursively yield LogRecords from all supported files in a directory."""
        logger.debug("Scanning directory", extra={"path": str(dir_path)})
        files_found = 0

        for file_path in dir_path.rglob("*"):
            if file_path.is_file() and file_path.suffix in self.supported_extensions:
                files_found += 1
                yield from self._read_file(file_path)

        if files_found == 0:
            logger.warning(
                "No supported log files found in directory",
                extra={"path": str(dir_path), "extensions": list(self.supported_extensions)},
            )

    def _read_file(self, file_path: Path) -> Iterator[LogRecord]:
        """Yield LogRecords from a single file."""
        logger.debug("Reading file", extra={"file": str(file_path)})
        is_json = file_path.suffix in (".json", ".jsonl", ".ndjson")

        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as f:
                for line_idx, line in enumerate(f, start=1):
                    raw_text = line.strip()
                    if not raw_text:
                        continue

                    # Truncate extremely long lines to avoid memory/regex blowups
                    if len(raw_text) > self.max_line_length:
                        logger.debug(
                            "Truncating long line",
                            extra={
                                "file": str(file_path),
                                "line": line_idx,
                                "length": len(raw_text),
                            },
                        )
                        raw_text = raw_text[: self.max_line_length]

                    metadata: dict[str, Any] = {}
                    if is_json:
                        try:
                            # Attempt to parse JSON to extract structured fields
                            parsed = json.loads(raw_text)
                            if isinstance(parsed, dict):
                                metadata = parsed
                                # Use the 'message' or 'msg' field as the primary text if present
                                raw_text = str(parsed.get("message", parsed.get("msg", raw_text)))
                        except json.JSONDecodeError:
                            # Fallback to treating it as plain text if JSON parsing fails
                            pass

                    yield LogRecord(
                        raw_text=raw_text,
                        source=str(file_path),
                        line_number=line_idx,
                        metadata=metadata,
                    )
        except OSError as e:
            raise IngestionError(
                f"Failed to read file: {e}",
                file=str(file_path),
                error=str(e),
            ) from e
