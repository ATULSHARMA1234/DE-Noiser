"""
API middleware for SemanticOS enterprise hardening.

Task 1: Correlation ID middleware — attaches a unique request_id to every request.
Task 3: Global exception handler — catches unhandled errors and returns clean JSON.
Task 4: Rate limiting — prevents abuse of the /ingest endpoint.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar

import redis.asyncio as redis_asyncio
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
    Redis-backed sliding window rate limiter for the /ingest endpoint.
    Gracefully falls back to local in-memory dict if Redis is down.
    """

    def __init__(self, app, max_requests: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = {}
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = redis_asyncio.from_url(redis_url, decode_responses=True)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only rate-limit the /ingest endpoint
        if request.url.path != "/ingest":
            return await call_next(request)

        # Prefer the proxy-set X-Forwarded-For client hop. Behind Caddy/nginx
        # request.client.host is the proxy, so without this every client shares
        # a single bucket and one abuser rate-limits everyone. (Trusts the first
        # XFF hop; correct behind exactly one trusted proxy.)
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            client_ip = xff.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - self.window_seconds

        use_fallback = False
        try:
            key = f"rate_limit:{client_ip}"
            async with self.redis.pipeline(transaction=True) as pipe:
                # Add current request using timestamp as score and value
                pipe.zadd(key, {str(now): now})
                # Remove elements older than the cutoff window
                pipe.zremrangebyscore(key, 0, cutoff)
                # Count current requests in this sliding window
                pipe.zcard(key)
                # Set dynamic TTL on the sliding window
                pipe.expire(key, self.window_seconds)
                
                results = await pipe.execute()
                recent_requests_count = results[2]

            if recent_requests_count > self.max_requests:
                rid = request_id_ctx.get("no-request")
                logger.warning("[%s] Redis rate limit exceeded for IP %s on /ingest", rid, client_ip)
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "Rate limit exceeded",
                        "detail": f"Maximum {self.max_requests} requests per {self.window_seconds}s",
                        "request_id": rid,
                    },
                )
        except Exception as e:
            logger.warning(f"Redis rate limiter failed, falling back to in-memory: {e}")
            use_fallback = True

        if use_fallback:
            # Bound memory: drop IPs whose entire window has expired. Without
            # this, every IP ever seen while Redis was down lingers forever.
            if len(self._requests) > 10_000:
                self._requests = {
                    ip: hits
                    for ip, ts in self._requests.items()
                    if (hits := [t for t in ts if t > cutoff])
                }

            # Clean old entries and count recent ones locally
            if client_ip not in self._requests:
                self._requests[client_ip] = []

            self._requests[client_ip] = [
                t for t in self._requests[client_ip] if t > cutoff
            ]

            if len(self._requests[client_ip]) >= self.max_requests:
                rid = request_id_ctx.get("no-request")
                logger.warning("[%s] In-memory rate limit exceeded for IP %s on /ingest", rid, client_ip)
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
