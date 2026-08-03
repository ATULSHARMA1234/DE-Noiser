"""Local sign-in, the brute-force throttle, refresh and logout.

Split out of `api.main`, which held eleven unrelated concerns in 1,500 lines:
every parallel feature branch touched that one file and every one of them
conflicted. A pure move — no handler below is changed, and the routes are the
same paths at the same methods.
"""

from __future__ import annotations

import contextlib
import time

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
)
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from denoiser import runtime
from denoiser.api.auth import DUMMY_PASSWORD_HASH as _DUMMY_PASSWORD_HASH
from denoiser.api.auth import (
    get_current_user,
    issue_token_pair,
    oauth2_scheme,
    require_role,
    revoke_token,
    rotate_refresh_token,
    verify_password,
)
from denoiser.api.cookies import set_session_cookies
from denoiser.api.routers_users import _same_tenant
from denoiser.api.schemas import (
    RefreshRequest,
    TokenResponse,
    UserLogin,
    UserResponse,
)
from denoiser.logging import get_logger
from denoiser.settings import get_settings as get_infra_settings
from denoiser.storage.db import User, get_db

logger = get_logger(__name__)

router = APIRouter(tags=["Auth"])


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


@router.post("/auth/login", response_model=TokenResponse)
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


@router.post("/admin/login-lockout/clear")
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


@router.post("/auth/refresh", response_model=TokenResponse)
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


@router.get("/auth/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Retrieve the current authenticated user's profile."""
    return current_user


@router.post("/auth/logout")
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


# The user directory moved to denoiser.api.routers_users.

# Health moved to denoiser.api.routers_health.
