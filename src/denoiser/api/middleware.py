"""
API middleware for SemanticOS enterprise hardening.

Task 1: Correlation ID middleware — attaches a unique request_id to every request.
Task 3: Global exception handler — catches unhandled errors and returns clean JSON.
Task 4: Rate limiting — prevents abuse of the /ingest endpoint.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# ── Task 1: Correlation ID Context ──────────────────────────────────────────

# Thread-safe context variable for the current request ID
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="no-request")

logger = logging.getLogger("denoiser.api")


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Attaches a unique X-Request-ID to every incoming request.
    The ID is stored in a ContextVar so all downstream loggers can access it.
    Also logs the request method, path, status code, and duration.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Use client-provided ID if present, otherwise generate one
        rid = request.headers.get("X-Request-ID", str(uuid.uuid4())[:12])
        request_id_ctx.set(rid)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        # Attach the ID to the response headers for traceability
        response.headers["X-Request-ID"] = rid

        # Structured request log
        logger.info(
            "[%s] %s %s → %d (%.1fms)",
            rid, request.method, request.url.path, response.status_code, duration_ms
        )

        return response


# ── Task 3: Global Exception Handler ────────────────────────────────────────

def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers that return clean JSON errors."""

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = request_id_ctx.get("no-request")
        logger.error(
            "[%s] Unhandled exception on %s %s: %s",
            rid, request.method, request.url.path, str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "detail": str(exc) if logger.isEnabledFor(logging.DEBUG) else "An unexpected error occurred",
                "request_id": rid,
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        rid = request_id_ctx.get("no-request")
        return JSONResponse(
            status_code=422,
            content={
                "error": "Validation error",
                "detail": str(exc),
                "request_id": rid,
            },
        )


# ── Task 4: Simple Rate Limiter ─────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory rate limiter for the /ingest endpoint.
    Limits to max_requests per window_seconds per client IP.
    """

    def __init__(self, app, max_requests: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only rate-limit the /ingest endpoint
        if request.url.path != "/ingest":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean old entries and count recent ones
        if client_ip not in self._requests:
            self._requests[client_ip] = []

        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if t > cutoff
        ]

        if len(self._requests[client_ip]) >= self.max_requests:
            rid = request_id_ctx.get("no-request")
            logger.warning("[%s] Rate limit exceeded for IP %s on /ingest", rid, client_ip)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "detail": f"Maximum {self.max_requests} requests per {self.window_seconds}s",
                    "request_id": rid,
                },
            )

        self._requests[client_ip].append(now)
        return await call_next(request)
