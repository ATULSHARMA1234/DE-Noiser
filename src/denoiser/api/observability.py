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

import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

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


def metrics_response() -> PlainTextResponse:
    """FastAPI handler body for GET /internal/metrics."""
    return PlainTextResponse(registry.render(), media_type="text/plain; version=0.0.4")
