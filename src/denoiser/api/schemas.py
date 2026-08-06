"""
Pydantic schemas for all API request/response models.

Task 2: Strongly-typed input validation replaces raw dict payloads.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── Analysis ─────────────────────────────────────────────────────────────────

class AnalysisRequest(BaseModel):
    source: str | None = Field(None, description="Path to log file or directory to analyze")
    sources: list[str] | None = Field(None, description="Multiple sources for cross-service correlation")
    baseline: str | None = Field(None, description="Path to a baseline index for anomaly comparison")
    intelligence: bool = Field(True, description="Enable LLM-based incident intelligence")
    top_n: int = Field(10, ge=1, le=100, description="Number of top clusters to return")

    @model_validator(mode="after")
    def _require_source(self) -> AnalysisRequest:
        if not self.source and not self.sources:
            raise ValueError("Either source or sources must be provided")
        return self


class ClusterResponse(BaseModel):
    id: int
    cluster_id: int
    size: int
    summary: str
    source: str
    representative_log: str
    representative_template: str
    anomaly_label: str = "known"
    anomaly_score: float = 0.0
    projection_2d: list[list[float]] | None = None


class AnalysisResponse(BaseModel):
    total_logs: int
    clusters: list[Any]
    intelligence: dict[str, Any] | None = None
    causal_links: list[dict[str, Any]] = Field(default_factory=list)
    metrics_context: dict[str, Any] | None = None
    timestamp: str


# ── Incidents ────────────────────────────────────────────────────────────────

class ResolveRequest(BaseModel):
    resolved: bool = Field(True, description="Set to true to resolve, false to reopen")


class IncidentResponse(BaseModel):
    id: int
    status: str
    title: str | None = None
    domain: str | None = None
    impact_score: float | None = None
    created_at: str | None = None
    resolved_at: str | None = None
    summary: Any | None = None
    remediation_hints: Any | None = None
    run_id: str | None = None
    source: str | None = None
    total_logs: int | None = None
    cluster_count: int | None = None


# ── Settings ─────────────────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    store_raw_logs: bool | None = None
    redact_pii: bool | None = None
    llm_model: str | None = None
    confidence_threshold: int | None = Field(None, ge=0, le=100)
    retention_days: int | None = Field(None, ge=1, le=365)
    sampling_threshold: int | None = Field(None, ge=1000)
    auto_analyze: bool | None = None
    slack_webhook_url: str | None = None
    s3_enabled: bool | None = None
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    sso_provider: str | None = None
    sso_client_id: str | None = None
    sso_metadata_url: str | None = None
    sso_enabled: bool | None = None


# ── Ingestion ────────────────────────────────────────────────────────────────

#: Maximum log entries accepted in a single /ingest call. Batches larger than
#: this are rejected rather than queued: one request holding hundreds of
#: thousands of entries occupies a worker for seconds and is indistinguishable
#: from a request that will never finish. Shippers batch; they do not need to
#: batch without bound.
MAX_INGEST_BATCH = int(os.getenv("SEMANTICOS_MAX_INGEST_BATCH", "10000"))

#: Maximum characters in a single log entry once serialised.
MAX_INGEST_ENTRY_CHARS = int(os.getenv("SEMANTICOS_MAX_INGEST_ENTRY_CHARS", "262144"))


class IngestPayload(BaseModel):
    """Structured ingestion payload. Accepts a list of log entries."""
    logs: list[Any] = Field(default_factory=list, description="Array of log entries (string or JSON objects)")

    @model_validator(mode="before")
    @classmethod
    def _coerce_input(cls, data: Any) -> Any:
        """
        Accept both payload shapes:
        - POST /ingest with a JSON list: `[{...}, {...}]`
        - POST /ingest with a wrapper dict: `{"logs": [{...}]}` (optional)
        - POST /ingest with a single log dict: `{"message": "..."}`
        """
        if data is None:
            return {"logs": []}
        if isinstance(data, list):
            return {"logs": data}
        if isinstance(data, dict):
            if "logs" in data:
                return data
            return {"logs": [data]}
        return {"logs": [data]}

    @model_validator(mode="after")
    def _validate_entries(self) -> IngestPayload:
        """Reject batches that are too large, and entries that are not logs.

        Previously any JSON value was accepted: ``[1, 2, null, true, [1,2]]``
        ingested as six "log lines", so the store filled with rows that no
        query could match and no parser could read. A log entry is either a
        line of text or a structured record — nothing else.
        """
        if len(self.logs) > MAX_INGEST_BATCH:
            raise ValueError(
                f"Batch of {len(self.logs)} entries exceeds the limit of "
                f"{MAX_INGEST_BATCH}; split it across multiple requests"
            )

        for index, entry in enumerate(self.logs):
            if isinstance(entry, str):
                if len(entry) > MAX_INGEST_ENTRY_CHARS:
                    raise ValueError(
                        f"Entry {index} is {len(entry)} characters, over the "
                        f"{MAX_INGEST_ENTRY_CHARS} limit"
                    )
                continue
            if isinstance(entry, dict):
                # Cheap proxy for serialised size; the exact byte count is not
                # worth a json.dumps of every entry on the hot path.
                if len(str(entry)) > MAX_INGEST_ENTRY_CHARS:
                    raise ValueError(
                        f"Entry {index} exceeds the {MAX_INGEST_ENTRY_CHARS} character limit"
                    )
                continue
            raise ValueError(
                f"Entry {index} is {type(entry).__name__}; each log entry must be "
                "a string or a JSON object"
            )
        return self


# ── Connectors ───────────────────────────────────────────────────────────────

class K8sFetchRequest(BaseModel):
    namespace: str
    pod_name: str


class AwsFetchRequest(BaseModel):
    log_group: str
    log_stream: str | None = None


class DockerFetchRequest(BaseModel):
    container_name: str


# ── Authentication ──────────────────────────────────────────────────────────

class UserLogin(BaseModel):
    email: str
    password: str
    # Only needed when one address and password authenticate in more than one
    # organisation — a consultant working for two customers on the same
    # deployment. Everyone else omits it and nothing about signing in changes.
    tenant: str | None = None


class UserResponse(BaseModel):
    id: int
    email: str
    role: str
    tenant_id: int | None = None
    is_active: bool = True
    department: str = "Engineering"
    environment_access: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    email: str
    password: str
    role: str = "VIEWER"  # VIEWER, ANALYST, ADMIN
    department: str = "Engineering"
    environment_access: list[str] = []


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    user: UserResponse


class RefreshRequest(BaseModel):
    # Optional: browsers hold the refresh token in an httpOnly cookie they
    # cannot read, so they post an empty body and the handler reads the cookie.
    # Programmatic clients still send it here.
    refresh_token: str | None = None
