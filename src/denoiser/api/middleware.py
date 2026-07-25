"""
API middleware for SemanticOS enterprise hardening.

Task 1: Correlation ID middleware — attaches a unique request_id to every request.
Task 3: Global exception handler — catches unhandled errors and returns clean JSON.
Task 4: Rate limiting — prevents abuse of the /ingest endpoint.
Task 5: Tenant quotas — a per-tenant ceiling across the whole API, so one
        workspace cannot exhaust the platform for every other workspace.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

import redis.asyncio as redis_asyncio
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
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

def _cors_headers(request: Request) -> dict[str, str]:
    """CORS headers for an error response, when the origin is allowlisted.

    An unhandled exception is turned into a response *above* CORSMiddleware in
    the stack, so it goes out with no CORS headers at all. The browser then
    reports "blocked by CORS policy" and the real status is invisible — a 500
    looks like a misconfigured API, and the actual bug stays hidden. Echoing the
    allowlisted origin here keeps errors legible in the UI without widening what
    is allowed.
    """
    origin = request.headers.get("origin")
    if not origin:
        return {}
    from denoiser.settings import get_settings

    allowed = get_settings().cors_origin_list
    if origin.rstrip("/") not in [o.rstrip("/") for o in allowed]:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }


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
            headers=_cors_headers(request),
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
            headers=_cors_headers(request),
        )


# ── Sliding window counter (shared by the IP limiter and tenant quotas) ─────

class SlidingWindowCounter:
    """Counts hits per key over a sliding window.

    Redis-backed so the window is shared across API replicas; falls back to a
    process-local dict when Redis is unreachable, which degrades the limit to
    per-replica rather than dropping it entirely.
    """

    def __init__(self, redis, window_seconds: int, max_tracked_keys: int = 10_000):
        self.redis = redis
        self.window_seconds = window_seconds
        self.max_tracked_keys = max_tracked_keys
        self._hits: dict[str, list[float]] = {}

    async def hit(self, key: str, now: float) -> tuple[int, bool]:
        """Record a hit and return ``(hits_in_window_including_this_one, used_fallback)``."""
        cutoff = now - self.window_seconds
        try:
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
            return int(results[2]), False
        except Exception as e:
            logger.warning(f"Redis rate limiter failed, falling back to in-memory: {e}")

        # Bound memory: drop keys whose entire window has expired. Without this,
        # every key ever seen while Redis was down lingers forever.
        if len(self._hits) > self.max_tracked_keys:
            self._hits = {
                k: recent
                for k, ts in self._hits.items()
                if (recent := [t for t in ts if t > cutoff])
            }

        recent = [t for t in self._hits.get(key, []) if t > cutoff]
        recent.append(now)
        self._hits[key] = recent
        return len(recent), True


def _limit_exceeded_response(detail: str, retry_after: int, limit: int, window: int) -> JSONResponse:
    rid = request_id_ctx.get("no-request")
    return JSONResponse(
        status_code=429,
        content={"error": "Rate limit exceeded", "detail": detail, "request_id": rid},
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Window": str(window),
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
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = redis_asyncio.from_url(redis_url, decode_responses=True)
        self._window = SlidingWindowCounter(self.redis, window_seconds)

    @property
    def _requests(self) -> dict[str, list[float]]:
        """Back-compat view of the in-memory fallback buckets."""
        return self._window._hits

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only rate-limit the /ingest endpoint
        if request.url.path != "/ingest":
            return await call_next(request)

        # `self.redis` may be replaced after construction (tests, reconfiguration).
        self._window.redis = self.redis

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
        count, used_fallback = await self._window.hit(f"rate_limit:{client_ip}", now)

        if count > self.max_requests:
            rid = request_id_ctx.get("no-request")
            logger.warning(
                "[%s] %s rate limit exceeded for IP %s on /ingest",
                rid, "In-memory" if used_fallback else "Redis", client_ip,
            )
            return _limit_exceeded_response(
                f"Maximum {self.max_requests} requests per {self.window_seconds}s",
                retry_after=self.window_seconds,
                limit=self.max_requests,
                window=self.window_seconds,
            )

        return await call_next(request)


# ── Task 5: Per-tenant API quota ────────────────────────────────────────────

# Tenant tiers and their default request ceilings per window. A per-IP limit
# does not bound a tenant: one workspace with a hundred pods ingesting from a
# hundred IPs is a hundred separate buckets. The quota below is keyed on the
# tenant itself, so a noisy or compromised workspace degrades only its own
# service.
DEFAULT_TENANT_QUOTAS: dict[str, int] = {
    "free": 600,
    "pro": 6_000,
    "enterprise": 60_000,
}

# Paths that must stay reachable even for a tenant that is over quota:
# liveness/readiness probes, scrape endpoints and the auth routes (which carry
# their own brute-force throttle) — a 429 there would lock an operator out of
# the workspace they are trying to fix.
QUOTA_EXEMPT_PATHS = frozenset({
    "/", "/health", "/healthz", "/readyz", "/metrics",
    "/docs", "/redoc", "/openapi.json",
    "/auth/login", "/auth/refresh", "/auth/logout",
})


class _TTLCache:
    """Tiny TTL cache so tenant resolution costs one query per key per period."""

    def __init__(self, ttl_seconds: float = 60.0, max_entries: int = 5_000):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, now: float) -> Any | None:
        entry = self._entries.get(key)
        if entry is None or entry[0] <= now:
            return None
        return entry[1]

    def set(self, key: str, value: Any, now: float) -> None:
        if len(self._entries) >= self.max_entries:
            self._entries = {k: v for k, v in self._entries.items() if v[0] > now}
            if len(self._entries) >= self.max_entries:
                self._entries.clear()
        self._entries[key] = (now + self.ttl, value)


class TenantQuotaMiddleware(BaseHTTPMiddleware):
    """Sliding-window request quota applied per tenant across the whole API.

    The tenant is resolved from whichever credential the request carries — the
    ``X-API-Key`` of a tenant, or the subject of a Bearer JWT — and the ceiling
    comes from that tenant's tier. Requests that carry no resolvable tenant are
    passed through: they are either unauthenticated (and will be rejected by the
    route's own dependency) or covered by the per-IP limiter.

    Enabled by default outside tests; set ``TENANT_QUOTA_ENABLED=false`` to turn
    it off, or ``TENANT_QUOTA_FREE`` / ``_PRO`` / ``_ENTERPRISE`` to retune the
    per-tier ceilings without a code change.
    """

    def __init__(
        self,
        app,
        quotas: dict[str, int] | None = None,
        window_seconds: int | None = None,
        enabled: bool | None = None,
    ):
        super().__init__(app)
        self.window_seconds = window_seconds or int(os.getenv("TENANT_QUOTA_WINDOW_SECONDS", "60"))
        self.quotas = dict(quotas or DEFAULT_TENANT_QUOTAS)
        if quotas is None:
            for tier in list(self.quotas):
                override = os.getenv(f"TENANT_QUOTA_{tier.upper()}")
                if override and override.isdigit():
                    self.quotas[tier] = int(override)
        self.enabled = self._resolve_enabled(enabled)
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = redis_asyncio.from_url(redis_url, decode_responses=True)
        self._window = SlidingWindowCounter(self.redis, self.window_seconds)
        self._tenants = _TTLCache()

    @staticmethod
    def _resolve_enabled(enabled: bool | None) -> bool:
        if enabled is not None:
            return enabled
        explicit = os.getenv("TENANT_QUOTA_ENABLED")
        if explicit is not None:
            return explicit.lower() in ("1", "true", "yes")
        from denoiser.settings import is_testing

        # Off under pytest so suites that fire hundreds of requests through a
        # single tenant are not throttled; on everywhere else.
        return not is_testing()

    def quota_for(self, tier: str | None) -> int:
        return self.quotas.get((tier or "free").lower(), self.quotas.get("free", 600))

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled or request.url.path in QUOTA_EXEMPT_PATHS:
            return await call_next(request)

        self._window.redis = self.redis
        now = time.time()
        resolved = await self._resolve_tenant(request, now)
        if resolved is None:
            return await call_next(request)

        tenant_id, tier = resolved
        limit = self.quota_for(tier)
        count, used_fallback = await self._window.hit(f"tenant_quota:{tenant_id}", now)

        if count > limit:
            rid = request_id_ctx.get("no-request")
            logger.warning(
                "[%s] %s tenant quota exceeded for tenant %s (tier=%s) on %s",
                rid, "In-memory" if used_fallback else "Redis", tenant_id, tier, request.url.path,
            )
            return _limit_exceeded_response(
                f"Tenant quota of {limit} requests per {self.window_seconds}s exceeded "
                f"for the '{tier}' tier",
                retry_after=self.window_seconds,
                limit=limit,
                window=self.window_seconds,
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
        response.headers["X-RateLimit-Window"] = str(self.window_seconds)
        return response

    async def _resolve_tenant(self, request: Request, now: float) -> tuple[str, str] | None:
        """Map the request's credential to ``(tenant_id, tier)``, or None."""
        api_key = request.headers.get("x-api-key")
        subject: str | None = None
        if not api_key:
            authorization = request.headers.get("authorization", "")
            if not authorization.lower().startswith("bearer "):
                return None
            subject = _jwt_subject(authorization.split(" ", 1)[1].strip())
            if not subject:
                return None

        cache_key = f"key:{api_key}" if api_key else f"sub:{subject}"
        cached = self._tenants.get(cache_key, now)
        if cached is not None:
            return cached or None  # a cached miss is stored as False

        resolved = await run_in_threadpool(_lookup_tenant, api_key, subject)
        self._tenants.set(cache_key, resolved or False, now)
        return resolved


def _jwt_subject(token: str) -> str | None:
    """Read ``sub`` from a JWT without hitting the database.

    Signature verification is the route dependency's job — this only needs a
    stable bucket key, and an unverifiable token gets rejected downstream
    anyway. Claims from it are never treated as authorisation.
    """
    try:
        from jose import jwt

        claims = jwt.get_unverified_claims(token)
    except Exception:
        return None
    sub = claims.get("sub")
    return str(sub) if sub else None


def _lookup_tenant(api_key: str | None, subject: str | None) -> tuple[str, str] | None:
    """Blocking tenant lookup, run in a worker thread."""
    from denoiser.storage.db import SessionLocal, Tenant, User

    db = SessionLocal()
    try:
        tenant = None
        if api_key:
            tenant = db.query(Tenant).filter(Tenant.api_key == api_key).first()
            if tenant is None:
                # Static ingest key (unattended shippers) maps to the first tenant,
                # matching verify_ingest_auth.
                static_key = os.getenv("INGEST_API_KEY")
                if static_key and api_key == static_key:
                    tenant = db.query(Tenant).order_by(Tenant.id).first()
        elif subject:
            user = db.query(User).filter(User.email == subject).first()
            if user is not None and user.tenant_id is not None:
                tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
                if tenant is None:
                    # A tenant row can be absent on legacy installs; still bucket
                    # the user's workspace rather than leaving it unbounded.
                    return (str(user.tenant_id), "free")
        if tenant is None:
            return None
        return (str(tenant.id), (tenant.tier or "free"))
    except Exception as e:
        logger.warning("Tenant quota lookup failed, request not counted: %s", e)
        return None
    finally:
        db.close()
