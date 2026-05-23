"""
Pydantic schemas for all API request/response models.

Task 2: Strongly-typed input validation replaces raw dict payloads.
"""

from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field


# ── Analysis ─────────────────────────────────────────────────────────────────

class AnalysisRequest(BaseModel):
    source: str = Field(..., description="Path to log file or directory to analyze")
    sources: Optional[List[str]] = Field(None, description="Multiple sources for cross-service correlation")
    baseline: Optional[str] = Field(None, description="Path to a baseline index for anomaly comparison")
    intelligence: bool = Field(True, description="Enable LLM-based incident intelligence")
    top_n: int = Field(10, ge=1, le=100, description="Number of top clusters to return")


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


class AnalysisResponse(BaseModel):
    total_logs: int
    clusters: List[Any]
    intelligence: Optional[Any] = None
    causal_links: Optional[List[Any]] = None
    timestamp: str


# ── Incidents ────────────────────────────────────────────────────────────────

class ResolveRequest(BaseModel):
    resolved: bool = Field(True, description="Set to true to resolve, false to reopen")


class IncidentResponse(BaseModel):
    id: int
    status: str
    title: Optional[str] = None
    domain: Optional[str] = None
    impact_score: Optional[float] = None
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None
    summary: Optional[Any] = None
    remediation_hints: Optional[Any] = None
    run_id: Optional[str] = None
    source: Optional[str] = None
    total_logs: Optional[int] = None
    cluster_count: Optional[int] = None


# ── Settings ─────────────────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    store_raw_logs: Optional[bool] = None
    redact_pii: Optional[bool] = None
    llm_model: Optional[str] = None
    confidence_threshold: Optional[int] = Field(None, ge=0, le=100)
    retention_days: Optional[int] = Field(None, ge=1, le=365)
    sampling_threshold: Optional[int] = Field(None, ge=1000)
    auto_analyze: Optional[bool] = None
    slack_webhook_url: Optional[str] = None


# ── Ingestion ────────────────────────────────────────────────────────────────

class IngestPayload(BaseModel):
    """Structured ingestion payload. Accepts a list of log entries."""
    logs: Optional[List[Any]] = Field(None, description="Array of log entries (string or JSON objects)")


# ── Connectors ───────────────────────────────────────────────────────────────

class K8sFetchRequest(BaseModel):
    namespace: str
    pod_name: str


class AwsFetchRequest(BaseModel):
    log_group: str
    log_stream: Optional[str] = None


class DockerFetchRequest(BaseModel):
    container_name: str
