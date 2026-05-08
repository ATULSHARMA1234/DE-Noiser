"""
Custom exception hierarchy for Semantic Log De-Noiser.

Every exception carries:
- A human-readable ``message``
- An integer ``exit_code`` for CLI process termination
- An optional ``context`` dict for structured debugging info

The CLI error handler uses ``exit_code`` to determine the process return code,
enabling CI pipelines to react to specific failure categories.
"""

from __future__ import annotations

from typing import Any


class DenoiserError(Exception):
    """Base exception for all denoiser errors.

    Parameters
    ----------
    message : str
        Human-readable error description.
    exit_code : int
        Suggested process exit code (defaults to 1 for generic errors).
    context : dict, optional
        Structured key-value context for debugging and logging.
    """

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.context = context or {}

    def __str__(self) -> str:
        base = self.message
        if self.context:
            details = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
            base = f"{base} [{details}]"
        return base


# ── Exit codes ───────────────────────────────────────────────────────────────
# Structured exit codes so CI systems can react to specific failure categories.
#
#   0  = success
#   1  = generic / unknown error
#   2  = configuration error
#   3  = ingestion error (bad file, unreadable, etc.)
#   4  = parsing error
#   5  = redaction error
#   6  = embedding error
#   7  = clustering error
#   8  = baseline error
#   9  = detection error
#  10  = anomaly threshold exceeded (--fail-on-anomaly triggered)

EXIT_CODE_SUCCESS = 0
EXIT_CODE_GENERIC = 1
EXIT_CODE_CONFIG = 2
EXIT_CODE_INGESTION = 3
EXIT_CODE_PARSING = 4
EXIT_CODE_REDACTION = 5
EXIT_CODE_EMBEDDING = 6
EXIT_CODE_CLUSTERING = 7
EXIT_CODE_BASELINE = 8
EXIT_CODE_DETECTION = 9
EXIT_CODE_ANOMALY_THRESHOLD = 10


# ── Specific exceptions ─────────────────────────────────────────────────────


class ConfigurationError(DenoiserError):
    """Raised when the configuration is invalid or incomplete."""

    def __init__(self, message: str, **ctx: Any) -> None:
        super().__init__(message, exit_code=EXIT_CODE_CONFIG, context=ctx)


class IngestionError(DenoiserError):
    """Raised when log ingestion fails (bad path, unreadable file, etc.)."""

    def __init__(self, message: str, **ctx: Any) -> None:
        super().__init__(message, exit_code=EXIT_CODE_INGESTION, context=ctx)


class ParsingError(DenoiserError):
    """Raised when a log line cannot be parsed into a structured record."""

    def __init__(self, message: str, **ctx: Any) -> None:
        super().__init__(message, exit_code=EXIT_CODE_PARSING, context=ctx)


class RedactionError(DenoiserError):
    """Raised when the redaction engine encounters an unrecoverable issue."""

    def __init__(self, message: str, **ctx: Any) -> None:
        super().__init__(message, exit_code=EXIT_CODE_REDACTION, context=ctx)


class EmbeddingError(DenoiserError):
    """Raised when embedding generation or caching fails."""

    def __init__(self, message: str, **ctx: Any) -> None:
        super().__init__(message, exit_code=EXIT_CODE_EMBEDDING, context=ctx)


class ClusteringError(DenoiserError):
    """Raised when the clustering algorithm fails or produces invalid output."""

    def __init__(self, message: str, **ctx: Any) -> None:
        super().__init__(message, exit_code=EXIT_CODE_CLUSTERING, context=ctx)


class BaselineError(DenoiserError):
    """Raised when baseline creation, loading, or inspection fails."""

    def __init__(self, message: str, **ctx: Any) -> None:
        super().__init__(message, exit_code=EXIT_CODE_BASELINE, context=ctx)


class DetectionError(DenoiserError):
    """Raised when anomaly detection or scoring fails."""

    def __init__(self, message: str, **ctx: Any) -> None:
        super().__init__(message, exit_code=EXIT_CODE_DETECTION, context=ctx)


class AnomalyThresholdExceeded(DenoiserError):
    """Raised when --fail-on-anomaly detects anomalies above the threshold.

    This is not a bug — it signals that the CI gate should fail the build.
    """

    def __init__(self, message: str, **ctx: Any) -> None:
        super().__init__(message, exit_code=EXIT_CODE_ANOMALY_THRESHOLD, context=ctx)
