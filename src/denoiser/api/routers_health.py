"""Liveness and readiness.

Split out of `api.main`, which had grown to 1,500 lines holding auth, users,
health, admin, metrics, ingest and the websocket. Every parallel feature branch
touched that file, and every one of them conflicted.

A pure move: the handlers below are unchanged, and the routes they serve are
the same paths at the same methods.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from denoiser import runtime
from denoiser.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["Health"])


# ─── HEALTH ───────────────────────────────────────────────────────────────────

@router.get("/health")
@router.get("/health/live")
def health_check():
    """Liveness: the process is up and serving. Cheap, no dependency I/O."""
    return {"status": "healthy", "version": "2.0.0"}


@router.get("/health/ready")
async def readiness_check(response: Response):
    """Readiness: probe every critical dependency and report per-component status.

    Returns 503 when any critical dependency is down so an orchestrator can pull
    the instance out of rotation instead of routing traffic into a broken pod.
    """
    checks: dict[str, str] = {}

    # Database (critical)
    try:
        from sqlalchemy import text

        from denoiser.storage.db import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = "ok"
        finally:
            db.close()
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Redis (critical: rate limiting, websocket fan-out)
    try:
        await runtime.redis_client().ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # ClickHouse (non-critical: log storage/query degrade, API still serves)
    checks["clickhouse"] = "ok" if runtime.clickhouse_store().client is not None else "unavailable"

    # Kafka producer (non-critical: falls back to direct ClickHouse insert)
    checks["kafka"] = "ok" if runtime.kafka_producer() is not None else "unavailable"

    # Ingestion consumer. Only meaningful when we are actually publishing to
    # Kafka — without a producer, /ingest writes straight to ClickHouse and the
    # consumer is not in the path. When it *is* in the path, a missing consumer
    # means every accepted write is silently unqueryable, so it is critical.
    consumer_required = runtime.kafka_producer() is not None
    if consumer_required:
        from denoiser.workers.heartbeat import evaluate_heartbeat, read_heartbeat

        consumer_ok, detail = evaluate_heartbeat(await read_heartbeat(runtime.redis_client()))
        checks["ingestion_consumer"] = detail
    else:
        consumer_ok = True
        checks["ingestion_consumer"] = "not_required (no Kafka producer; direct writes)"

    critical_ok = (
        checks["database"] == "ok" and checks["redis"] == "ok" and consumer_ok
    )
    if not critical_ok:
        response.status_code = 503
    return {"status": "ready" if critical_ok else "degraded", "checks": checks}
