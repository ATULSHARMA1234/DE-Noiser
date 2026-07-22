from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from denoiser.logging import get_logger

logger = get_logger(__name__)
import redis.asyncio as redis_asyncio

redis_client = redis_asyncio.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from denoiser.analysis.drift import ClusterSnapshot, DriftDetector
from denoiser.api.abac import require_abac
from denoiser.api.auth import create_access_token, get_current_user, require_role, verify_ingest_auth, verify_password
from denoiser.api.middleware import (
    CorrelationIDMiddleware,
    RateLimitMiddleware,
    register_exception_handlers,
)
from denoiser.api.scheduler import start_scheduler, stop_scheduler
from denoiser.api.schemas import (
    AnalysisRequest,
    IngestPayload,
    ResolveRequest,
    SettingsUpdate,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from denoiser.integrations.alert_router import (
    AlertPayload,
    ChannelType,
    WebhookConfig,
    alert_router,
)
from denoiser.storage.clickhouse_store import ClickHouseStore
from denoiser.storage.db import (
    AnalysisRun,
    Incident,
    User,
    get_db,
    init_db,
)
from denoiser.telemetry.ebpf_collector import EBPFCollector
from denoiser.telemetry.metrics_collector import MetricsCollector

# Background agents
metrics_agent = MetricsCollector()
ebpf_agent = EBPFCollector()
clickhouse_store = ClickHouseStore()

from aiokafka import AIOKafkaProducer

kafka_producer = None

app = FastAPI(title="SemanticOS — Enterprise Log Intelligence API", version="2.0.0")

# ── Enterprise Middleware Stack (Tasks 1, 3, 4) ──────────────────────────────
# Order matters: CORS first, then rate limiter, then correlation ID (outermost runs last)
# Origins are an explicit allowlist (never "*" on a credentialed API). Configure
# via CORS_ALLOWED_ORIGINS (comma-separated); defaults to local dev origins.
_cors_origins = [
    o.strip()
    for o in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "Bypass-Tunnel-Reminder"],
)
app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)
app.add_middleware(CorrelationIDMiddleware)

from denoiser.api.alerts import router as alerts_router
from denoiser.api.audit import AuditMiddleware
from denoiser.api.audit import router as audit_router
from denoiser.api.dashboards import router as dashboards_router
from denoiser.api.deployments import router as deployments_router
from denoiser.api.integrations import router as integrations_router
from denoiser.api.metrics import router as metrics_router
from denoiser.api.monitors import router as monitors_router
from denoiser.api.notebooks import router as notebooks_router
from denoiser.api.otlp import router as otlp_router
from denoiser.api.query import router as query_router
from denoiser.api.runbooks import router as runbooks_router
from denoiser.api.slo import router as slo_router
from denoiser.api.sso import router as sso_router
from denoiser.api.storage import router as storage_router
from denoiser.api.tracing import router as tracing_router

app.add_middleware(AuditMiddleware)
app.include_router(audit_router)
app.include_router(alerts_router)
app.include_router(tracing_router)
app.include_router(query_router)
app.include_router(slo_router)
app.include_router(dashboards_router)
app.include_router(metrics_router)
app.include_router(monitors_router)
app.include_router(runbooks_router)
app.include_router(integrations_router)
app.include_router(deployments_router)
app.include_router(sso_router)
app.include_router(otlp_router)
app.include_router(storage_router)
app.include_router(notebooks_router)

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
    "s3_endpoint": os.getenv("S3_ENDPOINT", "http://localhost:9000"),
    "s3_bucket": os.getenv("S3_BUCKET", "semanticos-logs"),
    # No credentials baked into source — supplied via env or the settings UI.
    "s3_access_key": os.getenv("S3_ACCESS_KEY", ""),
    "s3_secret_key": os.getenv("S3_SECRET_KEY", ""),
}


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return DEFAULT_SETTINGS.copy()


def _save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))


@app.on_event("startup")
async def on_startup():
    init_db()
    DATA_DIR.mkdir(exist_ok=True)
    if not SETTINGS_FILE.exists():
        _save_settings(DEFAULT_SETTINGS)
    metrics_agent.start()
    ebpf_agent.start()
    start_scheduler()

    global kafka_producer
    try:
        kafka_producer = AIOKafkaProducer(
            bootstrap_servers=os.getenv("KAFKA_BROKER", "localhost:9092")
        )
        await kafka_producer.start()
        logger.info("Kafka Producer started")
    except Exception as e:
        logger.error(f"Failed to start Kafka Producer: {e}")
        kafka_producer = None

@app.on_event("shutdown")
async def on_shutdown():
    metrics_agent.stop()
    ebpf_agent.stop()
    stop_scheduler()

    global kafka_producer
    if kafka_producer:
        await kafka_producer.stop()
        logger.info("Kafka Producer stopped")


# ─── MODELS — Now imported from denoiser.api.schemas ─────────────────────────


# ─── AUTHENTICATION ───────────────────────────────────────────────────────────

@app.post("/auth/login", response_model=TokenResponse)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    """Authenticate an operator and return an access token."""
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=401, detail="User account is deactivated")

    access_token = create_access_token(data={"sub": user.email})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }


@app.get("/auth/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Retrieve the current authenticated user's profile."""
    return current_user


@app.get("/users", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """List all registered operators."""
    return db.query(User).all()


@app.post("/users", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """Provision a new system operator."""
    exists = db.query(User).filter(User.email == payload.email).first()
    if exists:
        raise HTTPException(status_code=400, detail="User with this email already exists")

    from denoiser.api.auth import get_password_hash
    user = User(
        email=payload.email,
        hashed_password=get_password_hash(payload.password),
        role=payload.role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """Delete a system operator."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.email == current_user.email:
        raise HTTPException(status_code=400, detail="Cannot delete currently logged in admin user")

    db.delete(user)
    db.commit()
    return {"status": "deleted", "id": user_id}


@app.put("/users/{user_id}/deactivate")
def deactivate_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """Deactivate a system operator (soft deactivation)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.email == current_user.email:
        raise HTTPException(status_code=400, detail="Cannot deactivate currently logged in admin user")

    if user.email == "system-audit@semanticos.io":
        raise HTTPException(status_code=400, detail="Cannot deactivate the system-audit user")

    user.is_active = False
    db.commit()
    db.refresh(user)
    return {"status": "deactivated", "id": user_id, "is_active": user.is_active}


# ─── HEALTH ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    """Perform a system health check."""
    return {"status": "healthy", "version": "2.0.0"}


# ─── TELEMETRY — Live-ish host vitals (Task 16) ──────────────────────────────
@app.get("/vitals")
def get_vitals(limit: int = 20, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Returns the latest host telemetry points for dashboard sparkline charts.
    Backed by `data/metrics_stream.jsonl` written by `MetricsCollector` (Task 14).
    """
    try:
        if not metrics_agent.stream_path.exists():
            return {"status": "no_telemetry_available", "vitals": []}

        limit = max(1, min(int(limit), 120))
        buf: deque[dict[str, Any]] = deque(maxlen=limit)

        with open(metrics_agent.stream_path) as f:
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


@app.get("/metrics/current")
def get_metrics_current(limit: int = 20, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Alias for /vitals — returns latest host telemetry for dashboard sparklines.
    Compatible with Phase 3 telemetry integration (Task 16).
    """
    return get_vitals(limit=limit)


@app.get("/metrics/stream")
def get_metrics_stream(limit: int = 100, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """Return raw metrics stream entries for historical analysis."""
    try:
        if not metrics_agent.stream_path.exists():
            return {"status": "no_data", "entries": []}
        buf: deque[dict[str, Any]] = deque(maxlen=max(1, min(int(limit), 1000)))
        with open(metrics_agent.stream_path) as f:
            for line in f:
                if line.strip():
                    buf.append(json.loads(line))
        return {"status": "ok", "count": len(buf), "entries": list(buf)}
    except Exception as e:
        logger.error(f"Failed to load /metrics/stream: {e}")
        return {"status": "error", "message": str(e), "entries": []}


# ─── SOURCES — Dynamic file discovery + upload ───────────────────────────────

@app.get("/sources")
def list_sources(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
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
async def upload_source(file: UploadFile = File(...), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    """Upload a log file to the data/ directory for analysis."""
    # Reject path traversal: collapse to a bare filename and confirm the resolved
    # destination stays directly inside DATA_DIR.
    safe_name = os.path.basename(file.filename or "")
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    dest = (DATA_DIR / safe_name).resolve()
    if dest.parent != DATA_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid filename")
    content = await file.read()
    dest.write_bytes(content)
    stat = dest.stat()
    return {
        "name": safe_name,
        "path": str(dest),
        "size_bytes": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "status": "uploaded",
    }


@app.delete("/sources/{filename}")
def delete_source(filename: str, current_user: User = Depends(require_role(["ADMIN"]))):
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


# verify_ingest_auth is now imported from denoiser.api.auth


@app.post("/ingest")
async def ingest_logs(payload: IngestPayload, tenant_id: str = Depends(verify_ingest_auth)):
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

        # Hyperscale ingestion (Phase 24): Push to Redpanda/Kafka instead of ClickHouse directly
        if kafka_producer:
            for log_entry in body:
                payload_to_send = log_entry if isinstance(log_entry, dict) else {"raw_text": str(log_entry)}
                payload_to_send["_tenant_id"] = tenant_id
                msg_bytes = json.dumps(payload_to_send).encode('utf-8')
                await kafka_producer.send_and_wait("logs_topic", msg_bytes)
        else:
            # Fallback to direct ClickHouse insert if Kafka is unavailable
            if isinstance(body[0], dict):
                clickhouse_store.insert_logs(body, tenant_id=tenant_id)

        # Task 45: Publish to Redis Pub/Sub for horizontally scaled WebSockets
        try:
            for log_entry in body:
                msg = json.dumps(log_entry) if isinstance(log_entry, dict) else str(log_entry)
                await redis_client.publish(f"log_stream:{tenant_id}", msg)
        except Exception as re:
            logger.warning(f"Failed to publish ingest logs to Redis: {re}")

        return {"status": "success", "ingested": len(body)}
    except Exception as e:
        logger.exception("Ingest failed")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e!s}")


# ─── SLO / SLI ENDPOINTS ──────────────────────────────────────────────────────────




class LogQuery(BaseModel):
    query: str
    limit: int = 100
    from_ts: int | None = None
    to_ts: int | None = None

@app.post("/v1/logs/query")
def query_logs_api(payload: LogQuery, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """Execute a Log Query Language (LQL) search against the unified ClickHouse log stream."""
    try:
        results = clickhouse_store.query_logs(
            payload.query, 
            limit=payload.limit, 
            tenant_id=current_user.tenant_id,
            from_ts=payload.from_ts,
            to_ts=payload.to_ts
        )
        return {"status": "success", "count": len(results), "results": results}
    except Exception as e:
        logger.error(f"LQL Query failed: {e}")
        raise HTTPException(status_code=500, detail="Query execution failed")


# ─── CONNECTORS — Kubernetes, AWS, and Docker ───────────────────────────────

@app.get("/connectors/k8s/pods")
def list_k8s_pods(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
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
    except Exception:
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
async def fetch_k8s_logs(namespace: str = Form(...), pod_name: str = Form(...), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
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
    except Exception:
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
def list_aws_groups(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
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
    except Exception:
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
async def fetch_aws_logs(log_group: str = Form(...), log_stream: str | None = Form(None), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
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
    except Exception:
        # Simulated log generation
        simulated_logs = [
            "1715934500000\t[INFO]\tINIT\tContainer runtime: fargate-2.0",
            "1715934502000\t[INFO]\tSTART\tRequest ID: req-8219-cba0",
            "1715934505000\t[WARN]\tLATENCY\tDynamoDB batch_write took 450ms (threshold 100ms)",
            "1715934508000\t[ERROR]\tSNS\tFailed to publish event to topic: arn:aws:sns:us-east-1:123:notifications",
            "1715934510000\t[INFO]\tEND\tDuration: 520ms, Memory Used: 128MB",
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
def list_docker_containers(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
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
    except Exception:
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
async def fetch_docker_logs(container_name: str = Form(...), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
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
    except Exception:
        simulated_logs = [
            "node-api-1 | 2026-05-17 17:15:00 [info]: Express app listening on port 3000",
            "node-api-1 | 2026-05-17 17:15:02 [info]: Connected to PostgreSQL database at postgres-db:5432",
            "node-api-1 | 2026-05-17 17:15:04 [warn]: Redis cache connection missed for key 'user:123'",
            "node-api-1 | 2026-05-17 17:15:06 [error]: uncaughtException: Cannot read properties of undefined (reading 'email')",
            "node-api-1 | 2026-05-17 17:15:07 [info]: Process exited with code 1",
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
        with open(path) as f:
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
def get_settings(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    return _load_settings()


@app.put("/settings")
def update_settings(new_settings: SettingsUpdate, current_user: User = Depends(require_role(["ADMIN"]))):
    current = _load_settings()
    updates = new_settings.model_dump(exclude_unset=True)
    current.update(updates)
    _save_settings(current)
    return current


# ─── WEBSOCKET — Real-time log streaming ─────────────────────────────────────

@app.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket, token: str | None = None, db: Session = Depends(get_db)):
    await websocket.accept()

    # Prefer the token from the Authorization header (keeps it out of URLs/proxy
    # logs); fall back to the legacy query parameter for existing clients.
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        await websocket.close(code=4001, reason="Authentication token required")
        return

    try:
        user = get_current_user(token, db)
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # Task 45: Redis Pub/Sub for WebSockets — scoped per tenant so a subscriber
    # only ever receives its own tenant's log stream.
    channel = f"log_stream:{user.tenant_id}"
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)

    try:
        # We simulate the UI format expected by the frontend
        # The frontend expects {id, level, service, message, timestamp}
        line_id = 0
        async for message in pubsub.listen():
            if message["type"] == "message":
                line_id += 1
                try:
                    payload = json.loads(message["data"])
                    # Transform raw payload to UI expected format if needed
                    # If it's just raw log, make a best guess
                    level = "INFO"
                    raw_msg = str(payload.get("message", payload.get("log", str(payload))))
                    if "ERROR" in raw_msg.upper(): level = "ERROR"
                    elif "WARN" in raw_msg.upper(): level = "WARN"
                    elif "FATAL" in raw_msg.upper() or "CRITICAL" in raw_msg.upper(): level = "ANOMALY"

                    ws_msg = {
                        "id": str(line_id).zfill(4),
                        "level": payload.get("level", level).upper(),
                        "service": payload.get("service", "api"),
                        "message": raw_msg[:200],
                        "timestamp": payload.get("timestamp", time.time()),
                    }
                    await websocket.send_json(ws_msg)
                except Exception:
                    # Fallback for plain string
                    raw_msg = message["data"]
                    level = "INFO"
                    if "ERROR" in raw_msg.upper(): level = "ERROR"
                    elif "WARN" in raw_msg.upper(): level = "WARN"

                    await websocket.send_json({
                        "id": str(line_id).zfill(4),
                        "level": level,
                        "service": "unknown",
                        "message": raw_msg[:200],
                        "timestamp": time.time(),
                    })
    except WebSocketDisconnect:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await pubsub.unsubscribe(channel)
        await pubsub.close()



# ─── ANALYZE — Core analysis engine ──────────────────────────────────────────

@app.post("/analyze")
async def run_analysis(request: AnalysisRequest, current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    """
    Submit analysis to the Phase 4 Celery queue.

    Local development remains usable without Redis: if the broker cannot be
    reached, the same task body runs synchronously and returns the completed
    analysis response.
    """
    from kombu.exceptions import OperationalError

    from denoiser.workers.analysis_worker import run_analysis_task

    payload = request.model_dump()
    # Scope the resulting run/incidents to the requesting user's tenant so they
    # show up in the tenant-filtered /runs and /incidents views.
    payload["tenant_id"] = current_user.tenant_id

    # If running inside pytest, force synchronous execution for test compatibility
    if "PYTEST_CURRENT_TEST" in os.environ:
        result = run_analysis_task.apply(args=[payload])
        if result.failed():
            raise HTTPException(status_code=500, detail=str(result.result))
        res_data = result.result
        if isinstance(res_data, dict) and res_data.get("status") == "error":
            raise HTTPException(status_code=404, detail=res_data.get("message"))
        return res_data

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
def get_task_status(task_id: str, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
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
def get_incidents(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    incidents = db.query(Incident).filter(Incident.tenant_id == current_user.tenant_id).order_by(Incident.created_at.desc()).all()
    return [_incident_to_dict(inc) for inc in incidents]


@app.get("/incidents/{incident_id}")
def get_incident_detail(incident_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_abac("read", "incident"))):
    inc = db.query(Incident).filter(Incident.id == incident_id, Incident.tenant_id == current_user.tenant_id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _incident_to_dict(inc)


@app.put("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int, body: ResolveRequest, db: Session = Depends(get_db), current_user: User = Depends(require_abac("write", "incident"))):
    inc = db.query(Incident).filter(Incident.id == incident_id, Incident.tenant_id == current_user.tenant_id).first()
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
def delete_incident(incident_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_abac("delete", "incident"))):
    inc = db.query(Incident).filter(Incident.id == incident_id, Incident.tenant_id == current_user.tenant_id).first()
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

@app.get("/analysis/runs")
@app.get("/runs")
def list_analysis_runs(db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """List recent analysis runs."""
    runs = db.query(AnalysisRun).filter(AnalysisRun.tenant_id == current_user.tenant_id).order_by(AnalysisRun.created_at.desc()).all()
    return [_run_to_dict(r, db) for r in runs]


@app.get("/analysis/compare")
def compare_runs(run_a: str, run_b: str, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """Compare two analysis runs."""
    db_run_a = db.query(AnalysisRun).filter(AnalysisRun.id == run_a, AnalysisRun.tenant_id == current_user.tenant_id).first()
    db_run_b = db.query(AnalysisRun).filter(AnalysisRun.id == run_b, AnalysisRun.tenant_id == current_user.tenant_id).first()

    if not db_run_a or not db_run_b:
        raise HTTPException(status_code=404, detail="One or both runs not found")

    snap_a_data = db_run_a.clusters_snapshot or []
    snap_b_data = db_run_b.clusters_snapshot or []

    clusters_a = [
        ClusterSnapshot(
            cluster_id=d.get("cluster_id") or d.get("id") or 0,
            template=d.get("template") or d.get("representative_template") or "",
            size=d.get("size") or 0,
            anomaly_score=d.get("anomaly_score") or 0.0,
            priority=d.get("priority") or "P3",
            composite_severity_score=d.get("composite_severity_score") or 0.0,
            keyword_flag=d.get("keyword_flag") or False,
            summary=d.get("summary") or ""
        ) for d in snap_a_data
    ]
    clusters_b = [
        ClusterSnapshot(
            cluster_id=d.get("cluster_id") or d.get("id") or 0,
            template=d.get("template") or d.get("representative_template") or "",
            size=d.get("size") or 0,
            anomaly_score=d.get("anomaly_score") or 0.0,
            priority=d.get("priority") or "P3",
            composite_severity_score=d.get("composite_severity_score") or 0.0,
            keyword_flag=d.get("keyword_flag") or False,
            summary=d.get("summary") or ""
        ) for d in snap_b_data
    ]

    detector = DriftDetector()
    report = detector.compare(run_a, clusters_a, run_b, clusters_b)

    return report.to_dict()


@app.get("/analysis/runs/{run_id}")
@app.get("/runs/{run_id}")
def get_run_details(run_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_abac("read", "run"))):
    """Retrieve full analysis run details by ID."""
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id, AnalysisRun.tenant_id == current_user.tenant_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_dict(run, db)


@app.delete("/runs/{run_id}")
def delete_run(run_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """Delete an analysis run by ID."""
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id, AnalysisRun.tenant_id == current_user.tenant_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    db.delete(run)
    db.commit()
    return {"status": "deleted", "id": run_id}


def _run_to_dict(run: AnalysisRun, db: Session = None) -> dict:
    data = {
        "id": run.id,
        "source": run.source,
        "status": run.status,
        "raw_lines": run.raw_lines,
        "cluster_count": run.cluster_count,
        "reduction_ratio": run.reduction_ratio,
        "duration_sec": run.duration_sec,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "clusters_snapshot": run.clusters_snapshot,
    }
    if db:
        incident = db.query(Incident).filter(Incident.run_id == run.id).first()
        if incident:
            data["intelligence"] = {
                "failure_domain": incident.title,
                "incident_summary": incident.summary,
                "root_cause_hints": incident.remediation_hints,
            }
    return data


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
def list_webhooks(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """List all registered alert destinations."""
    return alert_router.list_destinations()


@app.post("/webhooks", status_code=201)
def create_webhook(body: WebhookCreateRequest, current_user: User = Depends(require_role(["ADMIN"]))):
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
def update_webhook(webhook_id: str, body: WebhookUpdateRequest, current_user: User = Depends(require_role(["ADMIN"]))):
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
def delete_webhook(webhook_id: str, current_user: User = Depends(require_role(["ADMIN"]))):
    """Remove an alert destination."""
    removed = alert_router.unregister(webhook_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "deleted", "id": webhook_id}


@app.post("/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: str, current_user: User = Depends(require_role(["ADMIN"]))):
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
def get_delivery_log(limit: int = 50, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """Return recent alert delivery audit records."""
    return alert_router.get_delivery_log(limit=limit)


# ─── ALERT TRIGGERS — Automated Runbooks ────────────────────────────────────

@app.post("/alerts/trigger")
def trigger_alert(alert: AlertPayload, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    """
    Receives an alert and triggers RunbookExecution if it's P0.
    In a real system, this could be triggered by internal analysis or external webhooks.
    """
    if alert.priority == "P0":
        from denoiser.automation.engine import process_incident

        # Check if an incident already exists for this run or create one
        incident = db.query(Incident).filter(Incident.analysis_run_id == alert.run_id).first()
        if not incident:
            incident = Incident(
                title=f"[P0] {alert.cluster_summary}",
                severity="P0",
                status="OPEN",
                analysis_run_id=alert.run_id,
                summary=alert.intelligence.get("incident_summary", alert.cluster_summary) if alert.intelligence else alert.cluster_summary,
            )
            db.add(incident)
            db.commit()
            db.refresh(incident)

        process_incident(db, incident)

    return {"status": "success", "alert_fingerprint": alert.fingerprint}
