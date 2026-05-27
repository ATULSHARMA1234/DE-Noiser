"""
Global configuration for Semantic Log De-Noiser.

Uses pydantic-settings so values can be overridden via environment variables
prefixed with ``SLD_`` (e.g. ``SLD_EMBEDDING_MODEL``).

Supports three analysis mode presets — ``general``, ``security``, and
``performance`` — each with tuned thresholds and clustering parameters.
Configuration can also be loaded from a ``.env`` file in the working directory.
"""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Enums ────────────────────────────────────────────────────────────────────


class AnalysisMode(str, enum.Enum):
    """Pre-configured analysis profiles."""

    GENERAL = "general"
    SECURITY = "security"
    PERFORMANCE = "performance"


class OutputFormat(str, enum.Enum):
    """Supported output formats."""

    TABLE = "table"
    JSON = "json"
    MARKDOWN = "markdown"


class AnomalySeverity(str, enum.Enum):
    """Severity levels for ``--fail-on-anomaly`` CI gating."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AnomalyLabel(str, enum.Enum):
    """Classification labels assigned to each log event after scoring."""

    KNOWN = "known"
    RARE_KNOWN = "rare_known"
    NEW_PATTERN = "new_pattern"
    HIGH_RISK_ANOMALY = "high_risk_anomaly"


class LogLevel(str, enum.Enum):
    """Supported verbosity levels for internal logging."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


# ── Mode presets ─────────────────────────────────────────────────────────────

MODE_PRESETS: dict[AnalysisMode, dict[str, Any]] = {
    AnalysisMode.GENERAL: {
        "min_cluster_size": 5,
        "min_samples": 3,
        "anomaly_threshold_low": 0.55,
        "anomaly_threshold_medium": 0.70,
        "anomaly_threshold_high": 0.85,
    },
    AnalysisMode.SECURITY: {
        # Tighter clusters and lower anomaly thresholds to flag subtle deviations
        "min_cluster_size": 3,
        "min_samples": 2,
        "anomaly_threshold_low": 0.40,
        "anomaly_threshold_medium": 0.55,
        "anomaly_threshold_high": 0.70,
    },
    AnalysisMode.PERFORMANCE: {
        # Larger clusters for high-volume metric/perf logs
        "min_cluster_size": 10,
        "min_samples": 5,
        "anomaly_threshold_low": 0.60,
        "anomaly_threshold_medium": 0.75,
        "anomaly_threshold_high": 0.90,
    },
}


# ── Main configuration ───────────────────────────────────────────────────────


class DenoiserConfig(BaseSettings):
    """Application-wide configuration with sensible defaults.

    Every field can be overridden via an environment variable with the ``SLD_``
    prefix (e.g. ``SLD_EMBEDDING_MODEL=paraphrase-MiniLM-L3-v2``).

    A ``.env`` file in the current working directory is loaded automatically.
    """

    model_config = SettingsConfigDict(
        env_prefix="SLD_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Logging ──────────────────────────────────────────────────────────
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Internal log verbosity: DEBUG, INFO, WARNING, ERROR.",
    )
    verbose: bool = Field(
        default=False,
        description="If True, override log_level to DEBUG.",
    )

    # ── Embedding ────────────────────────────────────────────────────────
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="SentenceTransformer model name for local embeddings.",
    )
    embedding_batch_size: int = Field(
        default=256,
        ge=1,
        le=4096,
        description="Batch size for embedding generation.",
    )
    embedding_dimension: int = Field(
        default=384,
        description="Dimensionality of the embedding vectors (must match model).",
    )

    # ── Clustering ───────────────────────────────────────────────────────
    min_cluster_size: int = Field(
        default=5,
        ge=2,
        description="HDBSCAN minimum cluster size.",
    )
    min_samples: int = Field(
        default=3,
        ge=1,
        description="HDBSCAN minimum samples for core points.",
    )
    cluster_metric: str = Field(
        default="euclidean",
        description="Distance metric for HDBSCAN (euclidean, cosine, manhattan).",
    )

    # ── Anomaly thresholds ───────────────────────────────────────────────
    anomaly_threshold_low: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="Cosine distance threshold for LOW anomaly classification.",
    )
    anomaly_threshold_medium: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Cosine distance threshold for MEDIUM anomaly classification.",
    )
    anomaly_threshold_high: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Cosine distance threshold for HIGH anomaly classification.",
    )

    # ── Paths ────────────────────────────────────────────────────────────
    cache_dir: Path = Field(
        default=Path.home() / ".cache" / "semantic-log-denoiser",
        description="Directory for embedding caches and temporary data.",
    )
    default_baseline_path: Path = Field(
        default=Path("baseline.index"),
        description="Default output path for baseline indexes.",
    )

    # ── Privacy ──────────────────────────────────────────────────────────
    redact_by_default: bool = Field(
        default=True,
        description="Whether to redact PII/secrets before external calls.",
    )
    local_only: bool = Field(
        default=False,
        description="If True, never send data to external embedding providers.",
    )

    # ── Output ───────────────────────────────────────────────────────────
    default_top_n: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Default number of top clusters/anomalies to show.",
    )
    default_format: OutputFormat = Field(
        default=OutputFormat.TABLE,
        description="Default output format: table, json, markdown.",
    )

    # ── Analysis mode ────────────────────────────────────────────────────
    mode: AnalysisMode = Field(
        default=AnalysisMode.GENERAL,
        description="Analysis mode preset: general, security, performance.",
    )

    # ── Ingestion ────────────────────────────────────────────────────────
    max_line_length: int = Field(
        default=10_000,
        ge=100,
        description="Maximum characters per log line before truncation.",
    )
    supported_extensions: list[str] = Field(
        default=[".log", ".txt", ".jsonl", ".ndjson", ".json"],
        description="File extensions the ingestion engine will process.",
    )

    # ── Intelligence (Phase 2) ───────────────────────────────────────────
    llm_enabled: bool = Field(
        default=False,
        description="Enable LLM-based incident summaries and root-cause hints.",
    )
    llm_model: str = Field(
        default="llama-3.3-70b-versatile",
        env="SLD_LLM_MODEL",
        description="The LLM model to use for intelligence.",
    )
    llm_api_key: str | None = Field(
        default=None,
        description="API key for the LLM provider.",
    )
    llm_base_url: str | None = Field(
        default=None,
        description="Optional base URL for OpenAI-compatible endpoints (e.g., local Ollama).",
    )

    # ── Validators ───────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _validate_thresholds(self) -> DenoiserConfig:
        """Ensure anomaly thresholds are strictly ordered: low < medium < high."""
        if not (
            self.anomaly_threshold_low
            < self.anomaly_threshold_medium
            < self.anomaly_threshold_high
        ):
            raise ValueError(
                f"Anomaly thresholds must be strictly ordered "
                f"(low < medium < high), got: "
                f"{self.anomaly_threshold_low}, "
                f"{self.anomaly_threshold_medium}, "
                f"{self.anomaly_threshold_high}"
            )
        return self

    @model_validator(mode="after")
    def _apply_verbose_override(self) -> DenoiserConfig:
        """When --verbose is set, force log level to DEBUG."""
        if self.verbose:
            self.log_level = LogLevel.DEBUG
        return self

    # ── Helpers ───────────────────────────────────────────────────────────

    def apply_mode_preset(self, mode: AnalysisMode | None = None) -> DenoiserConfig:
        """Apply a mode preset, overriding clustering and anomaly settings.

        Returns a **new** config instance with the preset values merged in.
        """
        target_mode = mode or self.mode
        preset = MODE_PRESETS.get(target_mode, {})
        overrides = {k: v for k, v in preset.items()}
        overrides["mode"] = target_mode
        return self.model_copy(update=overrides)

    def ensure_cache_dir(self) -> Path:
        """Create the cache directory if it does not exist and return its path."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir

    def privacy_summary(self) -> dict[str, bool | str]:
        """Return a dict summarizing the privacy posture for this run."""
        return {
            "redaction_enabled": self.redact_by_default,
            "local_only": self.local_only,
            "embedding_model": self.embedding_model,
            "data_sent_externally": not self.local_only,
        }


# ── Singleton ────────────────────────────────────────────────────────────────

settings = DenoiserConfig()
