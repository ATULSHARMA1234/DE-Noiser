from __future__ import annotations

import os
import glob
import json
import asyncio
import uuid
import time
import logging
from collections import deque
from pathlib import Path
from typing import Any, List, Optional

from denoiser.logging import get_logger

logger = get_logger(__name__)
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import polars as pl
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from denoiser.cli.main import Normalizer, Redactor, Deduplicator, LogReader, LocalEmbeddingProvider, LogClusterer, BaselineManager, AnomalyScorer, IncidentIntelligence
from denoiser.config import settings, AnalysisMode
from denoiser.storage.db import init_db, get_db, Incident, AnalysisRun
from denoiser.api.schemas import AnalysisRequest, AnalysisResponse, ResolveRequest, SettingsUpdate, IngestPayload
from denoiser.api.middleware import CorrelationIDMiddleware, RateLimitMiddleware, register_exception_handlers
from denoiser.ingestion.models import LogRecord
from denoiser.preprocessing.timestamp import TimestampExtractor
from denoiser.detection.causal_scorer import CausalScorer
from denoiser.detection.severity import SeverityScorer
from denoiser.integrations.alert_router import (
    AlertRouter, AlertPayload, WebhookConfig, ChannelType, alert_router
)
from denoiser.analysis.drift import DriftDetector, ClusterSnapshot
from denoiser.telemetry.metrics_collector import MetricsCollector
from denoiser.telemetry.ebpf_collector import EBPFCollector
from denoiser.detection.metrics_correlator import MetricsCorrelator
from denoiser.api.scheduler import start_scheduler, stop_scheduler
from denoiser.storage.clickhouse_store import ClickHouseStore

# Background agents
metrics_agent = MetricsCollector()
ebpf_agent = EBPFCollector()
clickhouse_store = ClickHouseStore()

app = FastAPI(title="SemanticOS — Enterprise Log Intelligence API", version="2.0.0")

# ── Enterprise Middleware Stack (Tasks 1, 3, 4) ──────────────────────────────
# Order matters: CORS first, then rate limiter, then correlation ID (outermost runs last)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)
app.add_middleware(CorrelationIDMiddleware)

# Register global exception handlers (Task 3)
register_exception_handlers(app)

# --- Data directory ---
DATA_DIR = Path("data")
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "store_raw_logs": True,
    "redact_pii": True,
    "llm_model": "llama-3.3-70b",
    "confidence_threshold": 70,
    "retention_days": 30,
    "sampling_threshold": 50000,
    "auto_analyze": False,
    "s3_enabled": False,
    "s3_endpoint": "http://localhost:9000",
    "s3_bucket": "semanticos-logs",
    "s3_access_key": "admin",
    "s3_secret_key": "password123",
}


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return DEFAULT_SETTINGS.copy()


def _save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))


@app.on_event("startup")
def on_startup():
    init_db()
    DATA_DIR.mkdir(exist_ok=True)
    if not SETTINGS_FILE.exists():
        _save_settings(DEFAULT_SETTINGS)
    metrics_agent.start()
    ebpf_agent.start()
    start_scheduler()

@app.on_event("shutdown")
def on_shutdown():
    metrics_agent.stop()
    ebpf_agent.stop()
    stop_scheduler()


# ─── MODELS — Now imported from denoiser.api.schemas ─────────────────────────


# ─── HEALTH ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "healthy", "version": "2.0.0"}


# ─── TELEMETRY — Live-ish host vitals (Task 16) ──────────────────────────────
@app.get("/vitals")
def get_vitals(limit: int = 20):
    """
    Returns the latest host telemetry points for dashboard sparkline charts.
    Backed by `data/metrics_stream.jsonl` written by `MetricsCollector` (Task 14).
    """
    try:
        if not metrics_agent.stream_path.exists():
            return {"status": "no_telemetry_available", "vitals": []}

        limit = max(1, min(int(limit), 120))
        buf: deque[dict[str, Any]] = deque(maxlen=limit)

        with open(metrics_agent.stream_path, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                payload = json.loads(line)
                buf.append(payload)

        vitals = []
        for m in list(buf):
            vitals.append(
                {
                    "timestamp": m.get("timestamp"),
                    "cpu": m.get("cpu_percent", 0),
                    "mem": m.get("memory_percent", 0),
                    "disk": m.get("disk_iops", 0),
                    # Dashboard expects "pkt/s"; our stream stores drops per second when available.
                    "net": m.get("network_drops_per_s", m.get("network_drops", 0)),
                }
            )

        return {"status": "ok", "vitals": vitals}
    except Exception as e:
        logger.error(f"Failed to load /vitals: {e}")
        return {"status": "error", "message": str(e), "vitals": []}


# ─── SOURCES — Dynamic file discovery + upload ───────────────────────────────

@app.get("/sources")
def list_sources():
    """List all log files in the data/ directory."""
    sources = []
    EXCLUDED = {"settings.json"}
    for ext in ["*.log", "*.txt", "*.json", "*.jsonl", "*.ndjson"]:
        for f in DATA_DIR.glob(ext):
            if f.name in EXCLUDED or f.suffix == ".db":
                continue
            stat = f.stat()
            sources.append({
                "name": f.name,
                "path": str(f),
                "size_bytes": stat.st_size,
                "size_human": _human_size(stat.st_size),
                "modified": stat.st_mtime,
                "lines_estimate": _estimate_lines(f, stat.st_size),
                "type": "file",
            })
    # Sort by modified time (newest first)
    sources.sort(key=lambda s: s["modified"], reverse=True)
    return sources


@app.post("/sources/upload")
async def upload_source(file: UploadFile = File(...)):
    """Upload a log file to the data/ directory for analysis."""
    dest = DATA_DIR / file.filename
    content = await file.read()
    dest.write_bytes(content)
    stat = dest.stat()
    return {
        "name": file.filename,
        "path": str(dest),
        "size_bytes": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "status": "uploaded",
    }


@app.delete("/sources/{filename}")
def delete_source(filename: str):
    """Delete a log file from the data/ directory."""
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = DATA_DIR / filename
    if not file_path.exists() or file_path.name == "settings.json" or file_path.suffix == ".db":
        raise HTTPException(status_code=404, detail="File not found or protected")
    
    try:
        file_path.unlink()
        return {"status": "deleted", "filename": filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest")
async def ingest_logs(payload: IngestPayload):
    """
    Standard HTTP ingestion endpoint.
    Accepts arrays of JSON logs (standard format from FluentBit / Vector).
    Writes them directly to data/live_stream.log with auto-rotation.
    """
    try:
        body = payload.logs
        if not body:
            raise HTTPException(status_code=400, detail="logs must not be empty")
            
        stream_file = DATA_DIR / "live_stream.log"
        
        # Auto-rotate if > 100MB
        if stream_file.exists() and stream_file.stat().st_size > 100 * 1024 * 1024:
            rotated_name = f"live_stream_{int(time.time())}.log"
            stream_file.rename(DATA_DIR / rotated_name)
            logger.info(f"Rotated live_stream.log to {rotated_name}")
        
        with open(stream_file, "a") as f:
            for log_entry in body:
                # Ensure it's a JSON string
                if isinstance(log_entry, dict):
                    f.write(json.dumps(log_entry) + "\n")
                else:
                    f.write(str(log_entry) + "\n")
        
        # Dual-write to ClickHouse for analytics (Task 40)
        # Assuming body is a list of dicts, if strings, we skip ClickHouse for now
        if isinstance(body[0], dict):
            clickhouse_store.insert_logs(body)
                    
        return {"status": "success", "ingested": len(body)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {str(e)}")


# ─── CONNECTORS — Kubernetes, AWS, and Docker ───────────────────────────────

@app.get("/connectors/k8s/pods")
def list_k8s_pods():
    """Discover K8s namespaces and pods. Falls back to mock if K8s is not available."""
    try:
        from kubernetes import client, config
        config.load_kube_config()
        v1 = client.CoreV1Api()
        pods = v1.list_pod_for_all_namespaces(limit=50)
        result = []
        for pod in pods.items:
            result.append({
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "status": pod.status.phase,
                "ip": pod.status.pod_ip,
            })
        return {"status": "connected", "pods": result}
    except Exception as e:
        # Fallback to simulated enterprise clusters for demo purposes
        return {
            "status": "simulated",
            "message": "Local kubeconfig not detected. Operating in high-fidelity sandbox mode.",
            "pods": [
                {"name": "auth-service-7f98c6", "namespace": "prod", "status": "Running", "ip": "10.244.0.12"},
                {"name": "payment-api-5b92d4", "namespace": "prod", "status": "Running", "ip": "10.244.0.15"},
                {"name": "ingress-nginx-controller-8a2b", "namespace": "ingress", "status": "Running", "ip": "10.244.1.2"},
                {"name": "db-backup-cron-9231", "namespace": "infra", "status": "Failed", "ip": "10.244.2.40"},
                {"name": "frontend-dashboard-f281", "namespace": "prod", "status": "Pending", "ip": "10.244.0.18"},
            ]
        }


@app.post("/connectors/k8s/fetch")
async def fetch_k8s_logs(namespace: str = Form(...), pod_name: str = Form(...)):
    """Fetch logs from K8s pod and save as a dynamic log source."""
    filename = f"k8s_{namespace}_{pod_name}.log"
    dest = DATA_DIR / filename
    
    try:
        from denoiser.integrations.k8s import KubernetesReader
        reader = KubernetesReader()
        records = list(reader.read(namespace, pod_name))
        
        # Write to file
        with open(dest, "w") as f:
            for r in records:
                f.write(r.raw_text + "\n")
        
        return {"status": "success", "source": filename, "lines": len(records)}
    except Exception as e:
        # Simulated log generation for sandbox demo
        simulated_logs = [
            f"2026-05-17T17:15:00Z [INFO] [{pod_name}] Starting bootstrap process...",
            f"2026-05-17T17:15:02Z [INFO] [{pod_name}] Loaded active configuration schema version 4.2.1",
            f"2026-05-17T17:15:05Z [WARNING] [{pod_name}] Slow connection detected to database replication secondary",
            f"2026-05-17T17:15:07Z [ERROR] [{pod_name}] Timeout accessing authentication microservice endpoint /verify",
            f"2026-05-17T17:15:10Z [FATAL] [{pod_name}] Process terminated unexpectedly: OutOfMemoryException (OOMKilled)",
        ]
        with open(dest, "w") as f:
            for line in simulated_logs:
                f.write(line + "\n")
                
        return {
            "status": "simulated",
            "message": "Local kubeconfig not detected. Generated sandbox log sequence.",
            "source": filename,
            "lines": len(simulated_logs)
        }


@app.get("/connectors/aws/groups")
def list_aws_groups():
    """Discover AWS CloudWatch log groups. Falls back to mock if AWS is not available."""
    try:
        import boto3
        client = boto3.client('logs')
        groups = client.describe_log_groups(limit=50)
        result = []
        for g in groups.get('logGroups', []):
            result.append({
                "name": g["logGroupName"],
                "arn": g["arn"],
                "stored_bytes": g.get("storedBytes", 0),
            })
        return {"status": "connected", "groups": result}
    except Exception as e:
        # Fallback to simulated CloudWatch log groups
        return {
            "status": "simulated",
            "message": "AWS credentials not detected. Operating in sandbox mode.",
            "groups": [
                {"name": "/aws/lambda/payment-processor-prod", "arn": "arn:aws:logs:us-east-1:123:log-group:1", "stored_bytes": 4510200},
                {"name": "/aws/ecs/api-gateway-cluster", "arn": "arn:aws:logs:us-east-1:123:log-group:2", "stored_bytes": 128990100},
                {"name": "/aws/rds/db-primary-logs", "arn": "arn:aws:logs:us-east-1:123:log-group:3", "stored_bytes": 452912800},
                {"name": "/aws/vpc/flow-logs-public", "arn": "arn:aws:logs:us-east-1:123:log-group:4", "stored_bytes": 10982991000},
            ]
        }


@app.post("/connectors/aws/fetch")
async def fetch_aws_logs(log_group: str = Form(...), log_stream: Optional[str] = Form(None)):
    """Fetch logs from AWS CloudWatch and save as a dynamic log source."""
    safe_name = log_group.replace("/", "_").strip("_")
    filename = f"aws_{safe_name}.log"
    dest = DATA_DIR / filename
    
    try:
        from denoiser.integrations.aws import CloudWatchReader
        reader = CloudWatchReader()
        records = list(reader.read(log_group, log_stream))
        
        with open(dest, "w") as f:
            for r in records:
                f.write(r.raw_text + "\n")
                
        return {"status": "success", "source": filename, "lines": len(records)}
    except Exception as e:
        # Simulated log generation
        simulated_logs = [
            f"1715934500000\t[INFO]\tINIT\tContainer runtime: fargate-2.0",
            f"1715934502000\t[INFO]\tSTART\tRequest ID: req-8219-cba0",
            f"1715934505000\t[WARN]\tLATENCY\tDynamoDB batch_write took 450ms (threshold 100ms)",
            f"1715934508000\t[ERROR]\tSNS\tFailed to publish event to topic: arn:aws:sns:us-east-1:123:notifications",
            f"1715934510000\t[INFO]\tEND\tDuration: 520ms, Memory Used: 128MB",
        ]
        with open(dest, "w") as f:
            for line in simulated_logs:
                f.write(line + "\n")
                
        return {
            "status": "simulated",
            "message": "AWS credentials not detected. Generated sandbox log sequence.",
            "source": filename,
            "lines": len(simulated_logs)
        }


@app.get("/connectors/docker/containers")
def list_docker_containers():
    """Discover running Docker containers on the host."""
    try:
        import docker
        client = docker.from_env()
        containers = client.containers.list(all=True)
        result = []
        for c in containers:
            result.append({
                "id": c.short_id,
                "name": c.name,
                "image": c.image.tags[0] if c.image.tags else "unknown",
                "status": c.status,
            })
        return {"status": "connected", "containers": result}
    except Exception as e:
        return {
            "status": "simulated",
            "message": "Docker socket not detected. Operating in sandbox mode.",
            "containers": [
                {"id": "a2b9f3", "name": "nginx-ingress", "image": "nginx:alpine", "status": "running"},
                {"id": "c7d2e4", "name": "redis-cache", "image": "redis:7-alpine", "status": "running"},
                {"id": "f8e1a6", "name": "postgres-db", "image": "postgres:15-alpine", "status": "running"},
                {"id": "d4c9b8", "name": "node-api", "image": "node:20-slim", "status": "exited"},
            ]
        }


@app.post("/connectors/docker/fetch")
async def fetch_docker_logs(container_name: str = Form(...)):
    """Fetch logs from a Docker container and save as a dynamic log source."""
    filename = f"docker_{container_name}.log"
    dest = DATA_DIR / filename
    
    try:
        import docker
        client = docker.from_env()
        container = client.containers.get(container_name)
        logs = container.logs(tail=1000).decode('utf-8')
        
        with open(dest, "w") as f:
            f.write(logs)
            
        return {"status": "success", "source": filename, "lines": len(logs.splitlines())}
    except Exception as e:
        simulated_logs = [
            f"node-api-1 | 2026-05-17 17:15:00 [info]: Express app listening on port 3000",
            f"node-api-1 | 2026-05-17 17:15:02 [info]: Connected to PostgreSQL database at postgres-db:5432",
            f"node-api-1 | 2026-05-17 17:15:04 [warn]: Redis cache connection missed for key 'user:123'",
            f"node-api-1 | 2026-05-17 17:15:06 [error]: uncaughtException: Cannot read properties of undefined (reading 'email')",
            f"node-api-1 | 2026-05-17 17:15:07 [info]: Process exited with code 1",
        ]
        with open(dest, "w") as f:
            for line in simulated_logs:
                f.write(line + "\n")
                
        return {
            "status": "simulated",
            "message": "Docker daemon not detected. Generated sandbox log sequence.",
            "source": filename,
            "lines": len(simulated_logs)
        }


def _human_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _estimate_lines(path: Path, size_bytes: int) -> int:
    """Estimate line count without reading the whole file."""
    if size_bytes == 0:
        return 0
    try:
        with open(path, "r") as f:
            sample = f.read(min(8192, size_bytes))
            lines_in_sample = sample.count("\n")
            if lines_in_sample == 0:
                return 1
            avg_line_len = len(sample) / lines_in_sample
            return int(size_bytes / avg_line_len)
    except Exception:
        return 0


# ─── SETTINGS — Persistent configuration ─────────────────────────────────────

@app.get("/settings")
def get_settings():
    return _load_settings()


@app.put("/settings")
def update_settings(new_settings: SettingsUpdate):
    current = _load_settings()
    updates = new_settings.model_dump(exclude_unset=True)
    current.update(updates)
    _save_settings(current)
    return current


# ─── WEBSOCKET — Real-time log streaming ─────────────────────────────────────

@app.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        # Check if there's a specific file to tail
        data = None
        try:
            data = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass

        target_file = data.get("file") if data else None

        if target_file and Path(target_file).exists():
            # Real file tailing mode
            await _tail_file(websocket, Path(target_file))
        else:
            # Demo stream mode — cycle through simulated production logs
            await _demo_stream(websocket)

    except WebSocketDisconnect:
        pass


async def _tail_file(ws: WebSocket, path: Path):
    """Tail a real log file and stream new lines as they appear."""
    line_id = 0
    with open(path, "r") as f:
        # Start from end of file
        f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                line_id += 1
                level = "INFO"
                if "ERROR" in line.upper():
                    level = "ERROR"
                elif "WARN" in line.upper():
                    level = "WARN"
                elif "FATAL" in line.upper() or "CRITICAL" in line.upper():
                    level = "ANOMALY"
                await ws.send_json({
                    "id": str(line_id).zfill(4),
                    "level": level,
                    "service": path.stem,
                    "message": line.strip()[:200],
                    "timestamp": time.time(),
                })
            else:
                await asyncio.sleep(0.5)


async def _demo_stream(ws: WebSocket):
    """Simulated production log stream for demo purposes."""
    demo_logs = [
        {"level": "INFO",  "service": "api-gateway",   "message": "GET /api/v2/users → 200 OK (12ms)"},
        {"level": "INFO",  "service": "auth-svc",      "message": "JWT token validated for user_8291"},
        {"level": "WARN",  "service": "worker-3",      "message": "Memory usage at 82% — approaching threshold"},
        {"level": "INFO",  "service": "api-gateway",   "message": "POST /api/v2/orders → 201 Created (45ms)"},
        {"level": "ERROR", "service": "db-primary",    "message": "Connection pool exhausted: 100/100 active connections"},
        {"level": "INFO",  "service": "cache-layer",   "message": "Redis PING → PONG (0.3ms)"},
        {"level": "WARN",  "service": "auth-svc",      "message": "Rate limit approaching for tenant org_42"},
        {"level": "INFO",  "service": "worker-1",      "message": "Job batch_export_7291 completed in 3.2s"},
        {"level": "ANOMALY", "service": "db-primary",  "message": "Replication lag detected: 4200ms behind primary", "highlight": True},
        {"level": "ERROR", "service": "api-gateway",   "message": "Upstream timeout: payment-svc did not respond in 5000ms"},
        {"level": "INFO",  "service": "scheduler",     "message": "Cron job cleanup_stale_sessions triggered"},
        {"level": "WARN",  "service": "worker-2",      "message": "Disk I/O latency spike: 340ms avg (normal: 12ms)"},
        {"level": "INFO",  "service": "api-gateway",   "message": "GET /api/v2/health → 200 OK (1ms)"},
        {"level": "ERROR", "service": "notification-svc", "message": "SMTP relay failed: connection refused to smtp.internal:587"},
        {"level": "INFO",  "service": "cache-layer",   "message": "Cache hit ratio: 94.2% (last 5m window)"},
        {"level": "ANOMALY", "service": "k8s-controller", "message": "Pod api-gateway-7f9d8 OOMKilled — restarting (attempt 3/5)", "highlight": True},
    ]
    line_id = 0
    while True:
        for log in demo_logs:
            line_id += 1
            await ws.send_json({
                "id": str(line_id).zfill(4),
                "timestamp": time.time(),
                **log,
            })
            await asyncio.sleep(1.5 + (0.5 if log["level"] == "INFO" else 0))


# ─── ANALYZE — Core analysis engine ──────────────────────────────────────────

@app.post("/analyze")
async def run_analysis(request: AnalysisRequest):
    """
    Submit analysis to the Phase 4 Celery queue.

    Local development remains usable without Redis: if the broker cannot be
    reached, the same task body runs synchronously and returns the completed
    analysis response.
    """
    from kombu.exceptions import OperationalError

    from denoiser.workers.analysis_worker import run_analysis_task

    payload = request.model_dump()
    try:
        async_result = run_analysis_task.delay(payload)
        return {"status": "queued", "task_id": async_result.id}
    except OperationalError as e:
        logger.warning(f"Celery broker unavailable; running analysis inline: {e}")
        result = run_analysis_task.apply(args=[payload])
        if result.failed():
            raise HTTPException(status_code=500, detail=str(result.result))
        return result.result


@app.get("/tasks/{task_id}")
def get_task_status(task_id: str):
    """Return Celery task state and the final analysis payload when complete."""
    from celery.result import AsyncResult

    from denoiser.workers.analysis_worker import celery_app

    result = AsyncResult(task_id, app=celery_app)
    response = {"task_id": task_id, "status": result.status}

    if result.status == "PROGRESS":
        response["meta"] = result.info or {}
    elif result.status == "SUCCESS":
        response["result"] = result.result
    elif result.status == "FAILURE":
        response["error"] = str(result.result)

    return response


# ─── INCIDENTS — CRUD + drill-down ───────────────────────────────────────────

@app.get("/incidents")
def get_incidents(db: Session = Depends(get_db)):
    incidents = db.query(Incident).order_by(Incident.created_at.desc()).all()
    return [_incident_to_dict(inc) for inc in incidents]


@app.get("/incidents/{incident_id}")
def get_incident_detail(incident_id: int, db: Session = Depends(get_db)):
    inc = db.query(Incident).filter(Incident.id == incident_id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _incident_to_dict(inc)


@app.put("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int, body: ResolveRequest, db: Session = Depends(get_db)):
    inc = db.query(Incident).filter(Incident.id == incident_id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    inc.status = "RESOLVED" if body.resolved else "OPEN"
    if body.resolved:
        import datetime
        inc.resolved_at = datetime.datetime.utcnow()
    else:
        inc.resolved_at = None
    db.commit()
    return _incident_to_dict(inc)


@app.delete("/incidents/{incident_id}")
def delete_incident(incident_id: int, db: Session = Depends(get_db)):
    inc = db.query(Incident).filter(Incident.id == incident_id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    db.delete(inc)
    db.commit()
    return {"status": "deleted", "id": incident_id}


def _incident_to_dict(inc: Incident) -> dict:
    return {
        "id": inc.id,
        "status": inc.status,
        "title": inc.title,
        "domain": inc.domain,
        "impact_score": inc.impact_score,
        "created_at": inc.created_at.isoformat() if inc.created_at else None,
        "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
        "summary": inc.summary,
        "remediation_hints": inc.remediation_hints,
        "run_id": inc.run_id if hasattr(inc, "run_id") else None,
        "source": inc.source if hasattr(inc, "source") else None,
        "total_logs": inc.total_logs if hasattr(inc, "total_logs") else None,
        "cluster_count": inc.cluster_count if hasattr(inc, "cluster_count") else None,
    }


# ─── RUNS — History ──────────────────────────────────────────────────────────

@app.get("/runs")
def get_runs(db: Session = Depends(get_db)):
    runs = db.query(AnalysisRun).order_by(AnalysisRun.created_at.desc()).all()
    return [_run_to_dict(r) for r in runs]


@app.get("/runs/compare")
def compare_runs(run_a: str, run_b: str, db: Session = Depends(get_db)):
    """Compare two analysis runs and return a DriftReport."""
    db_run_a = db.query(AnalysisRun).filter(AnalysisRun.id == run_a).first()
    db_run_b = db.query(AnalysisRun).filter(AnalysisRun.id == run_b).first()
    
    if not db_run_a or not db_run_b:
        raise HTTPException(status_code=404, detail="One or both runs not found")
        
    snap_a_data = db_run_a.clusters_snapshot or []
    snap_b_data = db_run_b.clusters_snapshot or []
    
    clusters_a = [ClusterSnapshot(**d) for d in snap_a_data]
    clusters_b = [ClusterSnapshot(**d) for d in snap_b_data]
    
    detector = DriftDetector()
    report = detector.compare(run_a, clusters_a, run_b, clusters_b)
    
    return report.to_dict()


@app.get("/runs/{run_id}")
def get_run_detail(run_id: str, db: Session = Depends(get_db)):
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_dict(run)


@app.delete("/runs/{run_id}")
def delete_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    db.delete(run)
    db.commit()
    return {"status": "deleted", "id": run_id}


def _run_to_dict(run: AnalysisRun) -> dict:
    return {
        "id": run.id,
        "source": run.source,
        "status": run.status,
        "raw_lines": run.raw_lines,
        "cluster_count": run.cluster_count,
        "reduction_ratio": run.reduction_ratio,
        "duration_sec": run.duration_sec,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


# ─── WEBHOOKS — Alert Routing CRUD (Task 15) ─────────────────────────────────

class WebhookCreateRequest(BaseModel):
    name: str
    channel_type: str
    url: str
    min_priority: str = "P1"
    enabled: bool = True
    extra: dict = {}


class WebhookUpdateRequest(BaseModel):
    name: str | None = None
    min_priority: str | None = None
    enabled: bool | None = None
    extra: dict | None = None


@app.get("/webhooks")
def list_webhooks():
    """List all registered alert destinations."""
    return alert_router.list_destinations()


@app.post("/webhooks", status_code=201)
def create_webhook(body: WebhookCreateRequest):
    """Register a new alert destination."""
    try:
        channel = ChannelType(body.channel_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid channel_type '{body.channel_type}'. Valid: slack, pagerduty, teams, generic")

    webhook_id = WebhookConfig.make_id(body.name, body.url)
    cfg = WebhookConfig(
        id=webhook_id,
        name=body.name,
        channel_type=channel,
        url=body.url,
        min_priority=body.min_priority,
        enabled=body.enabled,
        extra=body.extra,
    )
    alert_router.register(cfg)
    return {"status": "registered", **alert_router._config_to_dict(cfg)}


@app.put("/webhooks/{webhook_id}")
def update_webhook(webhook_id: str, body: WebhookUpdateRequest):
    """Update an existing webhook configuration."""
    cfg = alert_router.get_destination(webhook_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if body.name is not None:
        cfg.name = body.name
    if body.min_priority is not None:
        cfg.min_priority = body.min_priority
    if body.enabled is not None:
        cfg.enabled = body.enabled
    if body.extra is not None:
        cfg.extra = {**cfg.extra, **body.extra}
    return {"status": "updated", **alert_router._config_to_dict(cfg)}


@app.delete("/webhooks/{webhook_id}")
def delete_webhook(webhook_id: str):
    """Remove an alert destination."""
    removed = alert_router.unregister(webhook_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "deleted", "id": webhook_id}


@app.post("/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: str):
    """Fire a synthetic P1 test alert to a specific destination."""
    cfg = alert_router.get_destination(webhook_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Webhook not found")

    test_alert = AlertPayload(
        source="semanticos/test",
        run_id="test_run",
        priority="P1",
        cluster_id=0,
        cluster_summary="[TEST] SemanticOS webhook connectivity verification",
        representative_log="INFO [test] Alert routing system connectivity test - all channels operational",
        anomaly_score=0.72,
        causal_links=[],
        intelligence={
            "failure_domain": "Test Channel",
            "incident_summary": "This is a test alert from SemanticOS to verify webhook connectivity.",
            "root_cause_hints": ["No action required — this is a connectivity test."]
        },
        keyword_flag=False,
    )
    records = await alert_router._deliver_with_retry(cfg, test_alert)
    return {
        "status": records.status.value,
        "http_status": records.http_status,
        "latency_ms": records.latency_ms,
        "error": records.error,
    }


@app.get("/webhooks/log")
def get_delivery_log(limit: int = 50):
    """Return recent alert delivery audit records."""
    return alert_router.get_delivery_log(limit=limit)
