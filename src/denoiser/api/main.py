from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from denoiser.logging import get_logger
from denoiser.settings import get_settings as get_infra_settings
from denoiser.settings import validate_for_production

logger = get_logger(__name__)

# The process-wide handles live in denoiser.runtime, not here. While this module
# owned them it was the container every other module had to import — from inside
# a function body, because at module scope it was a cycle — and importing any
# router opened a Redis connection and issued ClickHouse DDL as a side effect.
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from denoiser import runtime
from denoiser.api import connectors as connectors_router
from denoiser.api import incidents as incidents_router
from denoiser.api import runs as runs_router
from denoiser.api import sources as source_registry
from denoiser.api import webhooks as webhooks_router
from denoiser.api.auth import DUMMY_PASSWORD_HASH as _DUMMY_PASSWORD_HASH
from denoiser.api.auth import get_current_user, issue_token_pair, oauth2_scheme, require_role, revoke_token, rotate_refresh_token, verify_ingest_auth, verify_password
from denoiser.api.cookies import set_session_cookies
from denoiser.api.middleware import (
    BodySizeLimitMiddleware,
    CorrelationIDMiddleware,
    CSRFMiddleware,
    RateLimitMiddleware,
    TenantQuotaMiddleware,
    register_exception_handlers,
)
from denoiser.api.pagination import MAX_PAGE_SIZE, ResourceId
from denoiser.api.scheduler import start_scheduler, stop_scheduler
from denoiser.api.schemas import (
    AnalysisRequest,
    IngestPayload,
    RefreshRequest,
    SettingsUpdate,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from denoiser.api.scope import TenantScope, tenant_predicate, tenant_scope
from denoiser.integrations.alert_router import (
    AlertPayload,
)
from denoiser.storage.db import (
    Incident,
    Tenant,
    User,
    get_db,
    init_db,
)
from denoiser.telemetry.ebpf_collector import EBPFCollector
from denoiser.telemetry.metrics_collector import MetricsCollector
from denoiser.utils.time import iso_utc, utcnow

# Background agents
metrics_agent = MetricsCollector()
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
app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)
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
from denoiser.api.observability import MetricsMiddleware, authorize_scrape, metrics_response
from denoiser.api.otlp import router as otlp_router
from denoiser.api.platform_admin import router as platform_router
from denoiser.api.query import router as query_router
from denoiser.api.runbooks import router as runbooks_router
from denoiser.api.scim import router as scim_router
from denoiser.api.slo import router as slo_router
from denoiser.api.sso import router as sso_router
from denoiser.api.storage import router as storage_router
from denoiser.api.tracing import router as tracing_router

app.add_middleware(AuditMiddleware)
# Outermost: time the full request (including every other middleware).
app.add_middleware(MetricsMiddleware)
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
from denoiser.api.platform_settings import save_settings as _save_settings


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


# ─── AUTHENTICATION ───────────────────────────────────────────────────────────

# ── Login brute-force throttle ───────────────────────────────────────────────
# The login route was previously unlimited (the RateLimitMiddleware only guards
# /ingest), leaving credential-stuffing unmitigated. Track failed attempts per
# (client IP, email) in Redis with an in-memory fallback, and lock the pair out
# once too many accumulate inside the window.
#
# The delay is *progressive* rather than a flat lockout. A flat "5 strikes and
# you are out for 5 minutes" is itself a denial of service: knowing a
# colleague's email address is enough to keep them locked out indefinitely by
# failing five logins every window. Escalating backoff still defeats credential
# stuffing — an attacker gets a handful of guesses an hour — while a legitimate
# operator who fat-fingers their password waits seconds, not minutes.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300

#: Delay applied after the Nth consecutive failure, in seconds. Index 0 is the
#: first failure past the free allowance.
LOGIN_BACKOFF_SCHEDULE = (5, 15, 60, 300, 900)

#: Failures allowed with no delay at all, for ordinary typos.
LOGIN_FREE_ATTEMPTS = 3

_login_attempts: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    """Best-effort client IP, preferring the proxy-set X-Forwarded-For hop."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _backoff_seconds(failures: int) -> int:
    """How long this (IP, email) pair must wait after ``failures`` failures."""
    if failures <= LOGIN_FREE_ATTEMPTS:
        return 0
    index = min(failures - LOGIN_FREE_ATTEMPTS - 1, len(LOGIN_BACKOFF_SCHEDULE) - 1)
    return LOGIN_BACKOFF_SCHEDULE[index]


async def _login_failures(key: str) -> list[float]:
    """Timestamps of recent failures for this key, newest last."""
    now = time.time()
    cutoff = now - LOGIN_WINDOW_SECONDS
    try:
        rk = f"login_fail:{key}"
        await runtime.redis_client().zremrangebyscore(rk, 0, cutoff)
        scores = await runtime.redis_client().zrange(rk, 0, -1, withscores=True)
        return sorted(float(score) for _member, score in scores)
    except Exception:
        recent = sorted(t for t in _login_attempts.get(key, []) if t > cutoff)
        _login_attempts[key] = recent
        return recent


async def _login_retry_after(key: str) -> int:
    """Seconds the caller must wait, or 0 if they may attempt a login now."""
    failures = await _login_failures(key)
    if not failures:
        return 0
    wait = _backoff_seconds(len(failures))
    if wait == 0:
        return 0
    elapsed = time.time() - failures[-1]
    remaining = wait - elapsed
    return int(remaining) + 1 if remaining > 0 else 0


async def _record_login_failure(key: str) -> None:
    now = time.time()
    try:
        rk = f"login_fail:{key}"
        await runtime.redis_client().zadd(rk, {f"{now}": now})
        await runtime.redis_client().expire(rk, LOGIN_WINDOW_SECONDS)
    except Exception:
        _login_attempts.setdefault(key, []).append(now)


async def _clear_login_failures(key: str) -> None:
    """Forget this key's failures — on success, or on an admin unlock."""
    with contextlib.suppress(Exception):
        await runtime.redis_client().delete(f"login_fail:{key}")
    _login_attempts.pop(key, None)


async def _clear_login_failures_for_email(email: str) -> int:
    """Clear every (IP, email) lockout for one account. Returns keys cleared.

    An operator locked out from an address they are no longer at cannot clear it
    themselves by waiting from a different IP, so an admin needs a way in.
    """
    cleared = 0
    suffix = f":{email.lower()}"
    try:
        async for rk in runtime.redis_client().scan_iter(match="login_fail:*"):
            if rk.endswith(suffix):
                await runtime.redis_client().delete(rk)
                cleared += 1
    except Exception:
        pass
    for key in [k for k in _login_attempts if k.endswith(suffix)]:
        _login_attempts.pop(key, None)
        cleared += 1
    return cleared


@app.post("/auth/login", response_model=TokenResponse)
async def login(payload: UserLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    """Authenticate an operator and return an access token."""
    if not get_infra_settings().local_login_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "Local password login is disabled; sign in through your "
                "organization's SSO. (MFA is enforced by the identity provider.)"
            ),
        )

    throttle_key = f"{_client_ip(request)}:{payload.email.lower()}"
    retry_after = await _login_retry_after(throttle_key)
    if retry_after > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed login attempts. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    # Both halves of this are blocking, and both are slow enough to matter: a
    # synchronous SQLAlchemy query, then a bcrypt verify that is ~100ms of pure
    # CPU by design. Run inline in this coroutine they stall the event loop for
    # the whole worker — no health check, no ingest, no websocket fan-out — so a
    # burst of sign-ins (Monday morning, or an IdP-initiated re-auth) degrades
    # every other endpoint. The threadpool is where the rest of this app's
    # blocking routes already run; the difference is only that they are `def`.
    #
    # A missing user still runs a verify against a dummy hash, so the response
    # time does not reveal whether an address is registered.
    def _authenticate() -> User | None:
        found = db.query(User).filter(User.email == payload.email).first()
        if not found:
            verify_password(payload.password, _DUMMY_PASSWORD_HASH)
            return None
        if not verify_password(payload.password, found.hashed_password):
            return None
        return found

    user = await run_in_threadpool(_authenticate)
    if not user:
        await _record_login_failure(throttle_key)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=401, detail="User account is deactivated")

    # A successful sign-in clears the backoff, so one bad day does not leave a
    # penalty hanging over the next attempt.
    await _clear_login_failures(throttle_key)

    tokens = issue_token_pair(user.email)
    # Set as httpOnly cookies for browsers, and still returned in the body for
    # programmatic clients (the CLI, shippers, CI) that cannot use a cookie jar.
    # The web client reads neither: it relies on the cookies alone, so an XSS
    # has nothing durable to steal.
    set_session_cookies(response, tokens["access_token"], tokens.get("refresh_token"))
    return {**tokens, "user": user}


class UnlockLoginRequest(BaseModel):
    email: str


@app.post("/admin/login-lockout/clear")
async def clear_login_lockout(
    payload: UnlockLoginRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Clear an account's login backoff.

    Scoped to the admin's own tenant: unlocking is a security action, and one
    tenant's admin has no business touching another tenant's accounts.
    """
    target = (
        db.query(User)
        .filter(User.email == payload.email, _same_tenant(current_user.tenant_id))
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    cleared = await _clear_login_failures_for_email(target.email)
    return {"status": "cleared", "email": target.email, "keys_cleared": cleared}


@app.post("/auth/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    db: Session = Depends(get_db),
):
    """Exchange a valid refresh token for a new access+refresh pair (rotation).

    The presented refresh token is single-use: it is revoked as part of this
    call, so a stolen token works at most once.

    The token may arrive in the body (programmatic clients) or in the refresh
    cookie (browsers, which cannot read the httpOnly cookie to put it in a body).
    """
    from denoiser.api.cookies import REFRESH_COOKIE

    presented = (payload.refresh_token if payload else None) or request.cookies.get(REFRESH_COOKIE)
    if not presented:
        raise HTTPException(status_code=401, detail="No refresh token provided")

    tokens, user = rotate_refresh_token(presented, db)
    set_session_cookies(response, tokens["access_token"], tokens.get("refresh_token"))
    return {**tokens, "user": user}


@app.get("/auth/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Retrieve the current authenticated user's profile."""
    return current_user


@app.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke the caller's tokens and clear the session cookies."""
    from denoiser.api.cookies import REFRESH_COOKIE, clear_session_cookies, token_from_cookie

    # Revoke whichever access token was presented, header or cookie.
    presented = token or token_from_cookie(request)
    if presented:
        revoke_token(presented, db)

    # The refresh token outlives the access token, so leaving it valid would
    # make "log out" mean "log out for thirty minutes".
    refresh_cookie = request.cookies.get(REFRESH_COOKIE)
    if refresh_cookie:
        revoke_token(refresh_cookie, db)

    clear_session_cookies(response)
    return {"status": "logged_out"}


# The user directory is the membership list of one organisation. Every lookup
# below is filtered by the caller's tenant, so an admin can only ever see and
# manage their own colleagues. Unfiltered, these four endpoints let one
# customer's admin enumerate, delete and deactivate another customer's staff.
def _same_tenant(tenant_id: int | None):
    """Predicate matching the users belonging to ``tenant_id``.

    Unassigned users form their own bucket: an admin without a tenant manages
    the users without one. The NULL handling that makes that work lives in
    `denoiser.api.scope`, which is where every other router gets it — this was
    a third independent copy of the same rule.
    """
    return tenant_predicate(User, tenant_id)


#: The actor every unattributed audit row is written against. Deleting or
#: deactivating it would break attribution for the whole deployment, so it is
#: protected unconditionally rather than relying on tenant scoping to hide it.
SYSTEM_AUDIT_EMAIL = "system-audit@semanticos.io"


def _tenant_user(db: Session, user_id: int, current_user: User) -> User:
    """Fetch a user *from the caller's own organisation*, or 404.

    Returning 404 rather than 403 for someone else's user is deliberate: a 403
    would confirm that the id exists, which is enough to enumerate another
    organisation's headcount.
    """
    # Looked up unscoped first, purely to enforce the platform-wide protection
    # below. The only fact this can reveal is which id belongs to a fixed,
    # seeded service account — not anything about another organisation.
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.email == SYSTEM_AUDIT_EMAIL:
        raise HTTPException(status_code=400, detail="Cannot modify the system-audit user")

    if user is None or not _in_tenant(user, current_user.tenant_id):
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _in_tenant(user: User, tenant_id: int | None) -> bool:
    return user.tenant_id == tenant_id


@app.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List the operators in the caller's organisation (paginated)."""
    return (
        db.query(User)
        .filter(_same_tenant(current_user.tenant_id))
        .order_by(User.id)
        .limit(limit)
        .offset(offset)
        .all()
    )


@app.post("/users", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """Provision a new operator inside the caller's organisation."""
    exists = db.query(User).filter(User.email == payload.email).first()
    if exists:
        raise HTTPException(status_code=400, detail="User with this email already exists")

    from denoiser.api.auth import get_password_hash
    user = User(
        email=payload.email,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
        # Inherited from the admin creating them, never taken from the request:
        # a client-supplied tenant would let an admin plant an account inside
        # another organisation. Without it the new account was orphaned with a
        # null tenant and could not see its own colleagues' work.
        tenant_id=current_user.tenant_id,
        department=payload.department,
        environment_access=payload.environment_access,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/users/{user_id}")
def delete_user(user_id: ResourceId, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """Delete an operator from the caller's organisation."""
    user = _tenant_user(db, user_id, current_user)

    if user.email == current_user.email:
        raise HTTPException(status_code=400, detail="Cannot delete currently logged in admin user")

    db.delete(user)
    db.commit()
    return {"status": "deleted", "id": user_id}


@app.put("/users/{user_id}/deactivate")
def deactivate_user(user_id: ResourceId, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """Deactivate an operator in the caller's organisation (soft deactivation)."""
    user = _tenant_user(db, user_id, current_user)

    if user.email == current_user.email:
        raise HTTPException(status_code=400, detail="Cannot deactivate currently logged in admin user")

    user.is_active = False
    db.commit()
    db.refresh(user)
    return {"status": "deactivated", "id": user_id, "is_active": user.is_active}


# ─── HEALTH ───────────────────────────────────────────────────────────────────

@app.get("/health")
@app.get("/health/live")
def health_check():
    """Liveness: the process is up and serving. Cheap, no dependency I/O."""
    return {"status": "healthy", "version": "2.0.0"}


@app.get("/health/ready")
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


@app.get("/admin/signing-keys")
def signing_key_status(current_user: User = Depends(require_role(["ADMIN"]))):
    """Which JWT signing key is active and which retired keys are still accepted.

    An operator rolling the secret needs to confirm the new key took effect on
    every replica before dropping the old one from JWT_SECRET_KEY_PREVIOUS.
    Key ids are truncated hashes — the secrets themselves are never exposed.
    """
    from denoiser.api.keys import get_keyring

    return get_keyring().describe()


@app.get("/admin/credentials")
def credential_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Rotation state of the long-lived credentials, without exposing any of them.

    Covers what /admin/signing-keys does for the JWT key: whether each shared
    secret is set, whether a superseded value is still being accepted, and when
    this tenant's API key was last rotated.
    """
    from denoiser.api.credentials import describe_static_rotation
    from denoiser.api.keys import get_keyring
    from denoiser.api.tenancy import describe_scim_token, normalise_domains

    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    now = utcnow()
    overlap_ends = tenant.api_key_previous_expires_at if tenant else None

    return {
        "jwt_signing_keys": get_keyring().describe(),
        "ingest_api_key": describe_static_rotation("INGEST_API_KEY"),
        "scim_bearer_token": describe_static_rotation("SCIM_BEARER_TOKEN"),
        # This organisation's own SCIM credential and the email domains that
        # route federated identities to it. Neither the token nor any other
        # organisation's domains are exposed.
        "organisation": {
            "name": tenant.name if tenant else None,
            "sso_domains": normalise_domains(tenant.sso_domains) if tenant else [],
        },
        "tenant_scim_token": describe_scim_token(tenant),
        "tenant_api_key": {
            "configured": bool(tenant and tenant.api_key),
            "last_rotated_at": iso_utc(tenant.api_key_rotated_at) if tenant else None,
            "previous_key_accepted_until": (
                iso_utc(overlap_ends) if overlap_ends and overlap_ends > now else None
            ),
        },
    }


@app.get("/admin/usage")
def usage_meters(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
    scope: TenantScope = Depends(tenant_scope),
):
    """Per-day ingest volume for the caller's tenant, and their retention tier.

    The meters were being written by a task no deployment ever started, and no
    endpoint read them — so metered usage existed only as a table definition.
    """
    from denoiser.storage.db import BillingMeter
    from denoiser.workers.billing_worker import (
        DEFAULT_RETENTION_DAYS,
        RETENTION_DAYS_BY_TIER,
    )

    days = max(1, min(days, 365))
    since = utcnow() - timedelta(days=days)

    meters = (
        scope.query(BillingMeter)
        .filter(BillingMeter.date >= since)
        .order_by(BillingMeter.date.desc())
        .all()
    )
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    tier = (tenant.tier if tenant else None) or "free"

    return {
        "tier": tier,
        "retention_days": RETENTION_DAYS_BY_TIER.get(tier.lower(), DEFAULT_RETENTION_DAYS),
        "window_days": days,
        "totals": {
            "logs": sum(m.total_logs_ingested or 0 for m in meters),
            "bytes": sum(m.total_bytes_ingested or 0 for m in meters),
            "traces": sum(m.total_traces_ingested or 0 for m in meters),
        },
        "daily": [
            {
                "date": iso_utc(m.date),
                "logs": m.total_logs_ingested or 0,
                "bytes": m.total_bytes_ingested or 0,
                "traces": m.total_traces_ingested or 0,
            }
            for m in meters
        ],
    }


@app.post("/admin/usage/recalculate")
def recalculate_usage(current_user: User = Depends(require_role(["ADMIN"]))):
    """Re-run today's metering now instead of waiting for the nightly pass.

    Retention is left alone: deleting data is the scheduled pass's job, not a
    side effect of asking for a fresh number.
    """
    from denoiser.workers.billing_worker import aggregate_billing

    return aggregate_billing(enforce_retention=False)


class RotateApiKeyRequest(BaseModel):
    # 0 revokes the old key immediately — the correct choice for a leak.
    overlap_hours: int = Field(default=24, ge=0, le=720)


@app.post("/admin/tenant/api-key/rotate")
def rotate_tenant_key(
    payload: RotateApiKeyRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Issue a new API key for the caller's tenant. The key is returned once.

    The superseded key keeps working for `overlap_hours` so log shippers can be
    updated one at a time; pass 0 to cut it off immediately.
    """
    from denoiser.api.credentials import rotate_tenant_api_key

    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    overlap = payload.overlap_hours if payload else 24
    new_key = rotate_tenant_api_key(db, tenant, overlap_hours=overlap)
    return {
        "status": "rotated",
        "api_key": new_key,
        "overlap_hours": overlap,
        "previous_key_accepted_until": iso_utc(tenant.api_key_previous_expires_at),
        "warning": "Store this key now — it is not retrievable again.",
    }


@app.post("/admin/tenant/api-key/revoke-previous")
def revoke_previous_tenant_key(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """End the overlap early, once every shipper carries the new key."""
    from denoiser.api.credentials import revoke_previous_api_key

    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    revoked = revoke_previous_api_key(db, tenant)
    return {"status": "revoked" if revoked else "no_previous_key"}


@app.get("/internal/metrics")
async def internal_metrics(request: Request):
    """Prometheus exposition of SemanticOS's own request rate, errors and latency.

    Gated on METRICS_TOKEN — see `authorize_scrape`. Left unauthenticated this
    hands out the deployment's route inventory and traffic profile.
    """
    authorize_scrape(request)

    # Dead-letter depth is read here rather than tracked in-process: the
    # records are quarantined by the ingestion worker, a different pod, so the
    # count only exists in Redis. Silent data loss with no series to alert on
    # is what makes it dangerous.
    from denoiser.workers.dead_letter import read_counters

    try:
        counters = await read_counters(runtime.redis_client())
    except Exception:
        counters = {"total": 0, "by_topic": {}}

    return metrics_response(dlq_counters=counters)


@app.get("/telemetry/kernel-events")
def kernel_events(
    limit: int = Query(200, ge=1, le=MAX_PAGE_SIZE),
    since_ms: int | None = None,
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """Kernel events (TCP retransmits, OOM kills) captured by the eBPF collector.

    The collector was running and writing these to disk with no reader anywhere
    in the codebase. They now feed anomaly correlation, and this exposes them
    directly.
    """
    from denoiser.telemetry.ebpf_collector import EVENT_TYPES, read_events

    limit = max(1, min(int(limit), 2000))
    events = read_events(since_ms=since_ms, limit=limit)

    counts = {name: 0 for name in EVENT_TYPES.values()}
    for event in events:
        name = event.get("event_name")
        if name in counts:
            counts[name] += 1

    return {
        # Distinguish "tracing is off" from "tracing is on and the kernel is quiet".
        "tracing_supported": ebpf_agent.is_supported,
        "tracing_active": ebpf_agent.is_supported and getattr(ebpf_agent, "_running", False),
        "counts": counts,
        "events": events,
    }


# ─── TELEMETRY — Live-ish host vitals (Task 16) ──────────────────────────────
@app.get("/vitals")
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


@app.get("/metrics/current")
def get_metrics_current(limit: int = Query(20, ge=1, le=MAX_PAGE_SIZE), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """
    Alias for /vitals — returns latest host telemetry for dashboard sparklines.
    Compatible with Phase 3 telemetry integration (Task 16).
    """
    return get_vitals(limit=limit)


@app.get("/metrics/stream")
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


# ─── SOURCES — Dynamic file discovery + upload ───────────────────────────────

#: Largest single log file accepted by upload. Bounded because the previous
#: implementation read the whole body into memory before writing any of it, so
#: one large upload could exhaust the API process.
MAX_UPLOAD_BYTES = int(os.getenv("SEMANTICOS_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
_UPLOAD_CHUNK = 1024 * 1024


@app.get("/sources")
def list_sources(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """List the log files this tenant may analyse."""
    sources = []
    for f in source_registry.list_sources(current_user.tenant_id):
        stat = f.stat()
        sources.append({
            "name": f.name,
            # Relative to the data root: the absolute path told every caller the
            # server's directory layout, and is not something the UI needs.
            "path": str(f.relative_to(Path.cwd())) if f.is_absolute() and str(f).startswith(str(Path.cwd())) else f.name,
            "size_bytes": stat.st_size,
            "size_human": _human_size(stat.st_size),
            "modified": stat.st_mtime,
            "lines_estimate": _estimate_lines(f, stat.st_size),
            "type": "file",
        })
    sources.sort(key=lambda s: s["modified"], reverse=True)
    return sources


@app.post("/sources/upload")
async def upload_source(file: UploadFile = File(...), current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))):
    """Upload a log file into this tenant's own source directory."""
    # Collapse to a bare filename: the destination directory is chosen by the
    # server from the authenticated tenant, never by the client.
    safe_name = os.path.basename(file.filename or "")
    if not safe_name or safe_name in (".", "..") or safe_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    dest_dir = source_registry.tenant_dir(current_user.tenant_id)
    dest = (dest_dir / safe_name).resolve()
    if dest.parent != dest_dir.resolve():
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Streamed in fixed chunks and aborted the moment the cap is passed, so an
    # oversized upload costs one chunk of memory rather than its whole size.
    written = 0
    try:
        with open(dest, "wb") as out:
            while chunk := await file.read(_UPLOAD_CHUNK):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds the {_human_size(MAX_UPLOAD_BYTES)} limit",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except OSError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to store upload: {e}")

    # Mirror to shared storage so every replica can see it, not just this pod.
    # A failure here fails the upload: reporting success for a file that only
    # one of N pods can read is worse than reporting the failure, because the
    # user finds out later, as an intermittently missing source.
    store = runtime.source_store()
    if store.enabled():
        try:
            await run_in_threadpool(store.put, current_user.tenant_id, safe_name, dest)
        except Exception as e:
            dest.unlink(missing_ok=True)
            logger.exception("Failed to mirror upload %s to shared storage", safe_name)
            raise HTTPException(
                status_code=503,
                detail=f"Upload could not be stored in shared storage: {e}",
            )

    return {
        "name": safe_name,
        "path": safe_name,
        "size_bytes": written,
        "size_human": _human_size(written),
        "status": "uploaded",
    }


@app.delete("/sources/{filename}")
def delete_source(filename: str, current_user: User = Depends(require_role(["ADMIN"]))):
    """Delete one of this tenant's own uploaded log files."""
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Only ever this tenant's own directory: the shared sample files are not
    # any one tenant's to remove, and another tenant's uploads are not visible.
    # Hydrated first so a delete issued against a replica that never cached the
    # file still finds it, rather than 404-ing on a file the user can see.
    source_registry.hydrate(filename, current_user.tenant_id)

    file_path = (source_registry.tenant_dir(current_user.tenant_id) / filename).resolve()
    if not file_path.is_file() or file_path.parent != source_registry.tenant_dir(current_user.tenant_id).resolve():
        raise HTTPException(status_code=404, detail="File not found or protected")

    try:
        file_path.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Shared copy last: deleting it while the local copy survives would leave
    # the file resurrectable on this pod but gone everywhere else. The other
    # replicas' caches are stale until they next hydrate, which is acceptable —
    # they hold no copy the bucket can restore.
    store = runtime.source_store()
    if store.enabled():
        store.delete(current_user.tenant_id, filename)

    return {"status": "deleted", "filename": filename}


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

        # Redact before anything is persisted. Redaction used to run only when
        # building embedding text during analysis, so the raw line — with its
        # SSNs, card numbers, tokens and passwords intact — was what actually
        # got written to disk and to ClickHouse, and what /v1/logs/query handed
        # back. Everything downstream of this point sees redacted content.
        from denoiser.api.platform_settings import (
            build_redactor,
            raw_log_storage_enabled,
        )
        from denoiser.preprocessing.redaction import redact_value

        redactor = build_redactor()
        body = [redact_value(entry, redactor) for entry in body]

        # Serialize each entry once and append the whole batch in a single write.
        serialized = [
            json.dumps(e) if isinstance(e, dict) else str(e)
            for e in body
        ]

        # `store_raw_logs` governs the redundant copy. When it is off, the
        # streaming and search paths still work; only the forensic copy is
        # skipped.
        #
        # The copy goes through the sink rather than straight to a local file:
        # a per-pod `data/live_stream.log` is what forced the API to a single
        # replica. See denoiser.storage.raw_log_sink.
        if raw_log_storage_enabled():
            await run_in_threadpool(runtime.raw_log_sink().write, tenant_id, serialized)

        # Hyperscale ingestion (Phase 24): Push to Redpanda/Kafka instead of ClickHouse directly.
        # send() enqueues without blocking on the broker ack; awaiting the futures
        # together lets aiokafka batch them into few round-trips. The previous
        # send_and_wait per message paid a full round-trip per log — the opposite
        # of hyperscale.
        if runtime.kafka_producer():
            futures = []
            for log_entry in body:
                payload_to_send = log_entry if isinstance(log_entry, dict) else {"raw_text": str(log_entry)}
                payload_to_send["_tenant_id"] = tenant_id
                msg_bytes = json.dumps(payload_to_send).encode('utf-8')
                futures.append(await runtime.kafka_producer().send("logs_topic", msg_bytes))
            if futures:
                await asyncio.gather(*futures)
        else:
            # Fallback to direct ClickHouse insert if Kafka is unavailable
            if isinstance(body[0], dict):
                runtime.clickhouse_store().insert_logs(body, tenant_id=tenant_id)

        # Task 45: Publish to Redis Pub/Sub for horizontally scaled WebSockets.
        # Pipelined so the fan-out is one round-trip, not one per log.
        try:
            async with runtime.redis_client().pipeline(transaction=False) as pipe:
                for msg in serialized:
                    pipe.publish(f"log_stream:{tenant_id}", msg)
                await pipe.execute()
        except Exception as re:
            logger.warning(f"Failed to publish ingest logs to Redis: {re}")

        return {"status": "success", "ingested": len(body)}
    except Exception as e:
        logger.exception("Ingest failed")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e!s}")


# ─── SLO / SLI ENDPOINTS ──────────────────────────────────────────────────────────




class LogQuery(BaseModel):
    query: str
    limit: int = Field(100, ge=1, le=MAX_PAGE_SIZE)
    from_ts: int | None = None
    to_ts: int | None = None

@app.post("/v1/logs/query")
def query_logs_api(payload: LogQuery, current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """Execute a Log Query Language (LQL) search against the unified ClickHouse log stream."""
    from denoiser.query.parser import QueryTooComplex

    try:
        results = runtime.clickhouse_store().query_logs(
            payload.query,
            limit=payload.limit,
            tenant_id=current_user.tenant_id,
            from_ts=payload.from_ts,
            to_ts=payload.to_ts
        )
        return {"status": "success", "count": len(results), "results": results}
    except QueryTooComplex as e:
        # A query the parser refuses is a bad request, not a server fault; it
        # previously surfaced as an opaque 500 with nothing actionable in it.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"LQL Query failed: {e}")
        raise HTTPException(status_code=500, detail="Query execution failed")


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
def update_settings(
    new_settings: SettingsUpdate,
    request: Request,
    current_user: User = Depends(require_role(["ADMIN"])),
):
    from denoiser.api.audit import diff_fields, record_changes

    current = _load_settings()
    updates = new_settings.model_dump(exclude_unset=True)

    # Capture what moved before the write. Settings govern retention and
    # redaction, so "someone changed settings and got a 200" is not a usable
    # audit record — the previous value is the part an investigation needs.
    changes = diff_fields(current, updates)
    _redact_secret_changes(changes)
    record_changes(request, changes)

    current.update(updates)
    _save_settings(current)
    return current


#: Settings whose values are credentials — recorded as changed, never with the
#: value itself, or the audit log becomes a place to read secrets from.
_SECRET_SETTING_KEYS = ("s3_secret_key", "s3_access_key", "slack_webhook_url", "sso_client_id")


def _redact_secret_changes(changes: dict) -> None:
    for key in list(changes):
        if key in _SECRET_SETTING_KEYS:
            changes[key] = {"from": "<redacted>", "to": "<redacted>"}


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
        # The browser cannot set headers on a WebSocket handshake, and the
        # session cookie is httpOnly so page script cannot put it in the query
        # string either. The handshake does carry cookies, so read it there.
        from denoiser.api.cookies import ACCESS_COOKIE

        token = websocket.cookies.get(ACCESS_COOKIE)

    if not token:
        await websocket.close(code=4001, reason="Authentication token required")
        return

    try:
        # Keyword arguments — positionally, `token` binds to the `request`
        # parameter and every websocket handshake is rejected as invalid.
        user = get_current_user(request=None, token=token, db=db)
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # Task 45: Redis Pub/Sub for WebSockets — scoped per tenant so a subscriber
    # only ever receives its own tenant's log stream.
    channel = f"log_stream:{user.tenant_id}"
    pubsub = runtime.redis_client().pubsub()
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

    # Resolve the sources here as well as in the worker. The worker is the
    # security boundary — it is what opens the file — but rejecting only there
    # means the caller gets "queued" for a request that was never going to run,
    # and has to poll a task id to discover a mistake the API already knew
    # about.
    #
    # A run with *some* readable sources still proceeds, matching the existing
    # multi-source contract: one unreachable service's log should not discard
    # the others. Only a request with nothing readable is refused outright.
    requested = list(request.sources or ([request.source] if request.source else []))
    resolution_errors: list[str] = []
    for src in requested:
        try:
            source_registry.resolve_source(str(src), current_user.tenant_id)
        except source_registry.SourceNotAllowed as e:
            resolution_errors.append(str(e))

    if requested and len(resolution_errors) == len(requested):
        # 404 rather than 400: resolve_source deliberately gives the same answer
        # for "outside the data root", "another tenant's file" and "no such
        # file", so that the endpoint cannot be used to probe for either. A
        # single not-found is the honest status for that single message.
        raise HTTPException(status_code=404, detail=resolution_errors[0])

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


# ─── ALERT TRIGGERS — Automated Runbooks ────────────────────────────────────

@app.post("/alerts/trigger")
def trigger_alert(alert: AlertPayload, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"])), scope: TenantScope = Depends(tenant_scope)):
    """
    Receives an alert and triggers RunbookExecution if it's P0.
    In a real system, this could be triggered by internal analysis or external webhooks.
    """
    if alert.priority == "P0":
        from denoiser.automation.engine import process_incident

        # Check if an incident already exists for this run or create one.
        # Column is `run_id`, not `analysis_run_id` — the old name did not exist
        # on the model and raised AttributeError before any P0 alert could land.
        incident = scope.query(Incident).filter(
            Incident.run_id == alert.run_id,
        ).first()
        if not incident:
            incident = Incident(
                tenant_id=current_user.tenant_id,
                title=f"[P0] {alert.cluster_summary}",
                severity="P0",
                impact_score=1.0,
                status="OPEN",
                run_id=alert.run_id,
                summary=alert.intelligence.get("incident_summary", alert.cluster_summary) if alert.intelligence else alert.cluster_summary,
            )
            db.add(incident)
            db.commit()
            db.refresh(incident)

        process_incident(db, incident)

    return {"status": "success", "alert_fingerprint": alert.fingerprint}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
