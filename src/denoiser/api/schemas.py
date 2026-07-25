"""
Pydantic schemas for all API request/response models.

Task 2: Strongly-typed input validation replaces raw dict payloads.
"""

from __future__ import annotations

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
    refresh_token: str
