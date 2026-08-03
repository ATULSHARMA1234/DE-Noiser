from __future__ import annotations

import os
from contextlib import asynccontextmanager

from denoiser.logging import get_logger
from denoiser.settings import get_settings as get_infra_settings
from denoiser.settings import validate_for_production

logger = get_logger(__name__)

# The process-wide handles live in denoiser.runtime, not here. While this module
# owned them it was the container every other module had to import — from inside
# a function body, because at module scope it was a cycle — and importing any
# router opened a Redis connection and issued ClickHouse DDL as a side effect.
from fastapi import (
    FastAPI,
)
from fastapi.middleware.cors import CORSMiddleware

from denoiser import runtime
from denoiser.api import connectors as connectors_router
from denoiser.api import incidents as incidents_router
from denoiser.api import runs as runs_router
from denoiser.api import sources as source_registry
from denoiser.api import webhooks as webhooks_router
from denoiser.api.middleware import (
    BodySizeLimitMiddleware,
    CorrelationIDMiddleware,
    CSRFMiddleware,
    RateLimitMiddleware,
    TenantQuotaMiddleware,
    register_exception_handlers,
)
from denoiser.api.scheduler import start_scheduler, stop_scheduler
from denoiser.storage.db import (
    init_db,
)
from denoiser.telemetry.ebpf_collector import EBPFCollector

# Background agents
ebpf_agent = EBPFCollector()

from aiokafka import AIOKafkaProducer

app = FastAPI(title="SemanticOS — Enterprise Log Intelligence API", version="2.0.0")

# ── Enterprise Middleware Stack (Tasks 1, 3, 4) ──────────────────────────────
# Order matters: CORS first, then rate limiter, then correlation ID (outermost runs last)
# Origins are an explicit allowlist (never "*" on a credentialed API). Configure
# via CORS_ALLOWED_ORIGINS (comma-separated); defaults to local dev origins.
_cors_origins = get_infra_settings().cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "Bypass-Tunnel-Reminder"],
)
# Coarse per-IP DoS guard on /ingest. Configurable, and much higher than the
# 100/minute it was hardcoded to: a single Fluent Bit shipper flushing once a
# second exhausts that budget in under two minutes, and behind a proxy every
# shipper shares one bucket. Per-*tenant* fairness is the TenantQuotaMiddleware
# below; this one only exists to stop a single address flooding the process.
#
# Found by load testing: at 100/minute the harness got 100 successes and 5,052
# rate-limited responses, so nothing downstream was being measured at all.
app.add_middleware(
    RateLimitMiddleware,
    max_requests=int(os.getenv("INGEST_RATE_LIMIT_REQUESTS", "6000")),
    window_seconds=int(os.getenv("INGEST_RATE_LIMIT_WINDOW_SECONDS", "60")),
)
# Per-tenant ceiling across every route. The /ingest IP limiter above does not
# bound a workspace (many pods, many IPs, one tenant), so without this a single
# tenant can still saturate the platform for everyone else.
app.add_middleware(TenantQuotaMiddleware)
# Outside the quota check so an oversized body is rejected on its Content-Length
# alone, without being read or counted against the tenant's budget.
app.add_middleware(CSRFMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(CorrelationIDMiddleware)

from denoiser.api.alerts import router as alerts_router
from denoiser.api.audit import AuditMiddleware
from denoiser.api.audit import router as audit_router
from denoiser.api.compat import router as compat_router
from denoiser.api.dashboards import router as dashboards_router
from denoiser.api.deployments import router as deployments_router
from denoiser.api.integrations import router as integrations_router
from denoiser.api.issues import router as issues_router
from denoiser.api.metrics import router as metrics_router
from denoiser.api.monitors import router as monitors_router
from denoiser.api.notebooks import router as notebooks_router
from denoiser.api.observability import MetricsMiddleware
from denoiser.api.otlp import router as otlp_router
from denoiser.api.platform_admin import router as platform_router
from denoiser.api.query import router as query_router
from denoiser.api.routers_admin import router as admin_router
from denoiser.api.routers_alerts_trigger import router as alerts_trigger_router
from denoiser.api.routers_analyze import router as analyze_router
from denoiser.api.routers_auth import router as auth_router
from denoiser.api.routers_health import router as health_router
from denoiser.api.routers_ingest import router as ingest_router
from denoiser.api.routers_query import router as log_query_router
from denoiser.api.routers_settings import router as settings_router
from denoiser.api.routers_sources import router as sources_router
from denoiser.api.routers_stream import router as stream_router
from denoiser.api.routers_telemetry import metrics_agent
from denoiser.api.routers_telemetry import router as telemetry_router
from denoiser.api.routers_users import router as users_router
from denoiser.api.runbooks import router as runbooks_router
from denoiser.api.scim import router as scim_router
from denoiser.api.slo import router as slo_router
from denoiser.api.sso import router as sso_router
from denoiser.api.storage import router as storage_router
from denoiser.api.tracing import router as tracing_router
from denoiser.api.versioning import VersionPrefixMiddleware

app.add_middleware(AuditMiddleware)
# Above the audit and quota layers so they see the resolved path: a request to
# /v1/users must be recorded, rate-limited and metered as /users, not as a
# second, separate endpoint. See denoiser.api.versioning.
app.add_middleware(VersionPrefixMiddleware, fastapi_app=app)
# Outermost: time the full request (including every other middleware).
app.add_middleware(MetricsMiddleware)
app.include_router(auth_router)
app.include_router(log_query_router)
app.include_router(admin_router)
app.include_router(sources_router)
app.include_router(ingest_router)
app.include_router(settings_router)
app.include_router(stream_router)
app.include_router(analyze_router)
app.include_router(alerts_trigger_router)
app.include_router(health_router)
app.include_router(users_router)
app.include_router(telemetry_router)
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
app.include_router(issues_router)
app.include_router(sso_router)
app.include_router(otlp_router)
app.include_router(storage_router)
app.include_router(notebooks_router)
app.include_router(scim_router)
app.include_router(platform_router)
app.include_router(compat_router)
# Registered in the order their routes were previously declared inline: FastAPI
# matches in registration order, and /webhooks/log sits behind
# /webhooks/{webhook_id} on purpose.
app.include_router(connectors_router.router)
app.include_router(incidents_router.router)
app.include_router(runs_router.router)
app.include_router(webhooks_router.router)

# Register global exception handlers (Task 3)
register_exception_handlers(app)

# --- Data directory ---
# Single source of truth for where log data lives, honouring the same
# SEMANTICOS_DATA_DIR the settings and source modules read. Hardcoding "data"
# here meant the test suite's redirected data directory was ignored and runs
# wrote into the developer's real one.
DATA_DIR = source_registry.DATA_DIR
SETTINGS_FILE = DATA_DIR / "settings.json"  # legacy; imported once, then unused

# Settings now live in the database so every API replica sees the same values.
from denoiser.api.platform_settings import load_settings as _load_settings


async def _startup() -> None:
    # Refuse to serve a production deployment with a configuration that is
    # silently unsafe. Doing this at boot rather than per-request means a bad
    # deploy fails immediately and visibly, instead of on whichever request
    # first reaches the affected subsystem.
    infra = get_infra_settings()
    if infra.is_production:
        problems = validate_for_production(infra)
        if problems:
            for problem in problems:
                logger.error(f"Unsafe production configuration: {problem}")
            raise RuntimeError(
                f"Refusing to start in production with {len(problems)} unsafe setting(s); see the errors above."
            )

    # Before anything else does work worth tracing. Off unless an OTLP endpoint
    # is configured, and it never raises — telemetry watches the work, it is
    # not the work. See denoiser.telemetry.otel.
    try:
        from denoiser.storage.db import engine as _db_engine
        from denoiser.telemetry.otel import configure as configure_tracing

        configure_tracing(app=app, engine=_db_engine)
    except Exception as e:
        logger.warning(f"Self-tracing setup skipped: {e}")

    init_db()
    DATA_DIR.mkdir(exist_ok=True)
    # Materialise the settings row (and import any legacy settings.json) so the
    # first replica to boot establishes them, not the first request to arrive.
    _load_settings()
    metrics_agent.start()
    ebpf_agent.start()
    start_scheduler()

    try:
        # Started here rather than built on demand: an AIOKafkaProducer must be
        # created inside the event loop that will use it, so its lifetime stays
        # with the lifespan and runtime only publishes it.
        producer = AIOKafkaProducer(
            bootstrap_servers=infra.kafka_broker or "localhost:9092"
        )
        await producer.start()
        runtime.set_kafka_producer(producer)
        logger.info("Kafka Producer started")
    except Exception as e:
        logger.error(f"Failed to start Kafka Producer: {e}")
        runtime.set_kafka_producer(None)


async def _shutdown() -> None:
    metrics_agent.stop()
    ebpf_agent.stop()
    stop_scheduler()

    producer = runtime.kafka_producer()
    if producer:
        await producer.stop()
        runtime.set_kafka_producer(None)
        logger.info("Kafka Producer stopped")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup/shutdown. Replaces the deprecated on_event hooks.

    The shutdown half runs in a finally block so a crash during serving still
    stops the collectors and flushes the Kafka producer, rather than leaving
    buffered records unsent.
    """
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


app.router.lifespan_context = lifespan


# ─── MODELS — Now imported from denoiser.api.schemas ─────────────────────────


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
