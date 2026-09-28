"""
Self-observability for the SemanticOS API.

An observability platform that cannot observe itself is a blind spot. This module
maintains in-process counters and a request-latency histogram (no external
dependency) and renders them in Prometheus text exposition format at
``/internal/metrics`` so a scraper can watch request rate, error rate and latency
of SemanticOS itself.

Cardinality is kept bounded by labelling on the *route template*
(``/incidents/{incident_id}``) rather than the resolved path, so a million
incident ids do not become a million time series.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from denoiser.settings import get_settings

# Histogram buckets in seconds (Prometheus convention, "+Inf" implied).
_BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class _Registry:
    """Minimal thread-safe metric store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (method, path, status) -> count
        self._requests: dict[tuple[str, str, int], int] = {}
        # (method, path) -> {"sum": float, "count": int, "buckets": [int]*len(_BUCKETS)}
        self._latency: dict[tuple[str, str], dict[str, Any]] = {}
        self._in_progress = 0

    def observe(self, method: str, path: str, status: int, duration_s: float) -> None:
        key_c = (method, path, status)
        key_l = (method, path)
        with self._lock:
            self._requests[key_c] = self._requests.get(key_c, 0) + 1
            slot = self._latency.get(key_l)
            if slot is None:
                slot = {"sum": 0.0, "count": 0, "buckets": [0] * len(_BUCKETS)}
                self._latency[key_l] = slot
            slot["sum"] += duration_s
            slot["count"] += 1
            for i, edge in enumerate(_BUCKETS):
                if duration_s <= edge:
                    slot["buckets"][i] += 1

    def inc_in_progress(self, delta: int) -> None:
        with self._lock:
            self._in_progress += delta

    def render(self) -> str:
        """Render the current state in Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            lines.append("# HELP semanticos_http_requests_total Total HTTP requests handled.")
            lines.append("# TYPE semanticos_http_requests_total counter")
            for (method, path, status), count in sorted(self._requests.items()):
                lines.append(
                    f'semanticos_http_requests_total{{method="{method}",path="{_esc(path)}",status="{status}"}} {count}'
                )

            lines.append("# HELP semanticos_http_requests_in_progress In-flight HTTP requests.")
            lines.append("# TYPE semanticos_http_requests_in_progress gauge")
            lines.append(f"semanticos_http_requests_in_progress {self._in_progress}")

            lines.append("# HELP semanticos_http_request_duration_seconds Request latency.")
            lines.append("# TYPE semanticos_http_request_duration_seconds histogram")
            for (method, path), slot in sorted(self._latency.items()):
                labels = f'method="{method}",path="{_esc(path)}"'
                # buckets[i] already holds the cumulative "<= edge[i]" count: each
                # observation increments every bucket whose edge it falls under.
                for i, edge in enumerate(_BUCKETS):
                    lines.append(
                        f'semanticos_http_request_duration_seconds_bucket{{{labels},le="{edge}"}} {slot["buckets"][i]}'
                    )
                lines.append(
                    f'semanticos_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {slot["count"]}'
                )
                lines.append(f'semanticos_http_request_duration_seconds_sum{{{labels}}} {slot["sum"]}')
                lines.append(f'semanticos_http_request_duration_seconds_count{{{labels}}} {slot["count"]}')
        rendered: str = "\n".join(lines) + "\n"
        return rendered


def _esc(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


registry = _Registry()


def _route_template(request: Request) -> str:
    """The low-cardinality route template, e.g. /incidents/{incident_id}."""
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return str(route.path)
    return request.url.path


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records request count, in-flight gauge and latency for every request."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # Don't recurse into the scrape endpoint's own accounting noise beyond a
        # single counter — but still time it; it's cheap and consistent.
        registry.inc_in_progress(1)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            registry.inc_in_progress(-1)
            registry.observe(request.method, _route_template(request), status, duration)


def metrics_token() -> str:
    """The bearer token a scraper must present, or "" if none is configured."""
    return os.getenv("METRICS_TOKEN", "").strip()


def authorize_scrape(request: Request) -> None:
    """Gate the scrape endpoint. Raises 401 when the caller cannot scrape.

    The rendered series name every route this deployment serves, its traffic
    volume and its error rate — a live map of the system for anyone who can
    reach the pod, which without a NetworkPolicy is every pod in the namespace.

    Policy is deliberately asymmetric, and matches how the rest of the platform
    treats unsafe configuration:

      METRICS_TOKEN set            -> require it, on every environment
      unset, non-production        -> open, so `curl :8000/internal/metrics`
                                      still works while developing
      unset, production            -> refuse, rather than serve the map because
                                      an operator forgot one variable

    The comparison is constant-time: a token checked with ``==`` leaks its
    prefix to anyone willing to time the responses.
    """
    expected = metrics_token()

    if not expected:
        if get_settings().is_production:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Metrics scraping requires METRICS_TOKEN in production. "
                    "Set it and configure the scraper's bearer token to match."
                ),
            )
        return

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented.strip(), expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing metrics scrape token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def render_dlq(counters: dict[str, Any]) -> list[str]:
    """Dead-letter counters as Prometheus series.

    A counter, not a gauge: the alertable signal is `increase(...) > 0` over a
    window — any record the ingestion pipeline could not write is a record the
    customer will never be able to query, and nothing else in the system says so.
    """
    total = int(counters.get("total") or 0)
    lines = [
        "# HELP semanticos_ingestion_dead_lettered_total Records quarantined by the ingestion worker.",
        "# TYPE semanticos_ingestion_dead_lettered_total counter",
        f"semanticos_ingestion_dead_lettered_total {total}",
    ]
    by_topic = counters.get("by_topic") or {}
    if by_topic:
        lines.append("# HELP semanticos_ingestion_dead_lettered_by_topic_total Quarantined records by source topic.")
        lines.append("# TYPE semanticos_ingestion_dead_lettered_by_topic_total counter")
        for topic, count in sorted(by_topic.items()):
            lines.append(
                f'semanticos_ingestion_dead_lettered_by_topic_total{{topic="{_esc(topic)}"}} {int(count)}'
            )
    return lines


def render_consumer(heartbeat: dict[str, Any] | None) -> list[str]:
    """Ingestion consumer liveness and backlog as Prometheus series.

    The consumer is a separate pod and exposes no HTTP surface of its own, so
    its state reaches a scraper only through the heartbeat the API already
    reads for readiness. Without this, "the consumer stopped" is visible on one
    readiness probe and nowhere a dashboard or an alert can see it.

    Lag is only emitted when it is actually known. Reporting an unknown lag as
    zero would read as "fully caught up", which is the opposite of the truth.
    """
    lines = [
        "# HELP semanticos_ingestion_consumer_up Whether the ingestion consumer has a fresh heartbeat.",
        "# TYPE semanticos_ingestion_consumer_up gauge",
    ]
    if not heartbeat:
        lines.append("semanticos_ingestion_consumer_up 0")
        return lines

    age = max(0.0, time.time() - float(heartbeat.get("at", 0)))
    lines.append("semanticos_ingestion_consumer_up 1")
    lines.append(
        "# HELP semanticos_ingestion_heartbeat_age_seconds Age of the consumer's last heartbeat."
    )
    lines.append("# TYPE semanticos_ingestion_heartbeat_age_seconds gauge")
    lines.append(f"semanticos_ingestion_heartbeat_age_seconds {age:.3f}")

    lag = heartbeat.get("lag")
    if isinstance(lag, int):
        lines.append("# HELP semanticos_ingestion_consumer_lag Uncommitted records behind the log head.")
        lines.append("# TYPE semanticos_ingestion_consumer_lag gauge")
        lines.append(f"semanticos_ingestion_consumer_lag {lag}")
    return lines


def metrics_response(
    dlq_counters: dict[str, Any] | None = None,
    consumer_heartbeat: dict[str, Any] | None = None,
    include_consumer: bool = False,
) -> PlainTextResponse:
    """FastAPI handler body for GET /internal/metrics."""
    body = registry.render()
    if dlq_counters is not None:
        body = body + "\n".join(render_dlq(dlq_counters)) + "\n"
    if include_consumer:
        body = body + "\n".join(render_consumer(consumer_heartbeat)) + "\n"
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")
