"""Host vitals and the metric stream.

Split out of `api.main`. A pure move; the handlers are unchanged.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, Query

from denoiser.api.auth import require_role
from denoiser.api.pagination import MAX_PAGE_SIZE
from denoiser.logging import get_logger
from denoiser.storage.db import User
from denoiser.telemetry.metrics_collector import MetricsCollector

logger = get_logger(__name__)

router = APIRouter(tags=["Telemetry"])

#: One collector per process, as before. `api.main` starts and stops it in the
#: application lifespan; this module only reads what it has written.
metrics_agent = MetricsCollector()


# ─── TELEMETRY — Live-ish host vitals (Task 16) ──────────────────────────────
@router.get("/vitals")
def get_vitals(limit: int = Query(20, ge=1, le=MAX_PAGE_SIZE), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Returns the latest host telemetry points for dashboard sparkline charts.
    Backed by `data/metrics_stream.jsonl` written by `MetricsCollector` (Task 14).

    These are the vitals of the node running SemanticOS, not of the services it
    monitors. The response says so explicitly — unlabelled, a CPU spike here
    reads as a spike in the customer's own infrastructure.
    """
    scope = {
        "scope": "semanticos_api_host",
        "host": metrics_agent.host,
        "description": "Vitals of the SemanticOS API host, not the monitored fleet.",
    }
    try:
        if not metrics_agent.enabled:
            return {"status": "disabled", "vitals": [], **scope}
        if not metrics_agent.stream_path.exists():
            return {"status": "no_telemetry_available", "vitals": [], **scope}

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

        return {"status": "ok", "vitals": vitals, **scope}
    except Exception as e:
        logger.error(f"Failed to load /vitals: {e}")
        return {"status": "error", "message": str(e), "vitals": [], **scope}


@router.get("/metrics/current")
def get_metrics_current(limit: int = Query(20, ge=1, le=MAX_PAGE_SIZE), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Alias for /vitals — returns latest host telemetry for dashboard sparklines.
    Compatible with Phase 3 telemetry integration (Task 16).
    """
    return get_vitals(limit=limit)


@router.get("/metrics/stream")
def get_metrics_stream(limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
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
