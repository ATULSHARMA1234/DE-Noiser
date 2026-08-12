"""
Cookie-based session transport, and the CSRF defence it requires.

Tokens used to be handed to the browser in the response body and kept in
``localStorage``. Anything that can run script on the page can read
``localStorage``, so a single XSS — in a dependency, in a markdown renderer, in
a log line rendered without escaping — yields a token valid for its full
lifetime, exfiltrated silently. An ``httpOnly`` cookie is not readable from
JavaScript at all, so the same XSS can act as the user *while they are on the
page* but cannot walk away with a credential.

That trade brings CSRF into scope: cookies are attached by the browser
automatically, including on requests originated by another site. Two mitigations
are applied together.

``SameSite=Lax`` stops the cookie being sent on cross-site POSTs at all, which
covers the classic form-submission attack. On top of that, a double-submit CSRF
token: a *readable* cookie whose value the client echoes in a header. An
attacker on another origin can cause the cookie to be sent but cannot read it to
construct the matching header.

Bearer-token clients — the CLI, log shippers, anything server-side — are
unaffected: the check applies only to requests that authenticated with a cookie
and carry no ``Authorization`` header, because a browser never attaches that
header on its own.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Request, Response

from denoiser.api.auth import ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_MINUTES
from denoiser.settings import get_settings

ACCESS_COOKIE = "sos_access"
REFRESH_COOKIE = "sos_refresh"
CSRF_COOKIE = "sos_csrf"
CSRF_HEADER = "X-CSRF-Token"

#: Methods that can change state, and so require a CSRF token.
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

#: Paths exempt from the CSRF check. Login and refresh are how a session is
#: established in the first place, so there is no session to forge yet; the
#: ingest endpoints authenticate with an API key, not a cookie.
CSRF_EXEMPT_PATHS = (
    "/auth/login",
    "/auth/refresh",
    "/auth/sso/callback",
    "/auth/sso/saml/acs",
    "/ingest",
    "/v1/logs",
    "/v1/traces",
    "/traces/v1/traces",
    "/scim/v2/Users",
    "/scim/v2/Groups",
    "/services/collector",
)


def _same_site() -> str:
    """The ``SameSite`` policy for session cookies.

    ``lax`` is right when the UI and API share an origin, which is what the
    bundled nginx/compose deployment does — the cookie is never sent
    cross-site, and CSRF is largely foreclosed before the token check runs.

    A split-origin deployment (UI on a CDN, API on its own host) needs ``none``,
    because the browser treats those as different sites and would otherwise drop
    the cookie on every API call. ``none`` requires ``Secure``, and leans on the
    double-submit CSRF token for the protection ``lax`` was providing.
    """
    configured = os.getenv("SEMANTICOS_COOKIE_SAMESITE", "lax").strip().lower()
    return configured if configured in ("lax", "strict", "none") else "lax"


def _secure_cookies() -> bool:
    """Whether to mark cookies ``Secure``.

    On in production, off in development — a Secure cookie is not stored at all
    over plain http, which would make local development impossible. Forced on
    whenever SameSite=None, which browsers reject without it.
    """
    return get_settings().is_production or _same_site() == "none"


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_session_cookies(response: Response, access_token: str, refresh_token: str | None) -> str:
    """Attach the session cookies. Returns the CSRF token that was set.

    The access and refresh cookies are ``httpOnly``; the CSRF cookie is
    deliberately *not*, because the client has to read it to echo it back.
    It carries no authority on its own.
    """
    secure = _secure_cookies()
    same_site = _same_site()
    csrf_token = issue_csrf_token()

    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=secure,
        samesite=same_site,
        path="/",
    )
    if refresh_token:
        response.set_cookie(
            REFRESH_COOKIE,
            refresh_token,
            max_age=REFRESH_TOKEN_EXPIRE_MINUTES * 60,
            httponly=True,
            secure=secure,
            samesite=same_site,
            # Scoped to the one endpoint that consumes it, so it is not attached
            # to every ordinary API call and cannot leak through one.
            path="/auth",
        )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        httponly=False,
        secure=secure,
        samesite=same_site,
        path="/",
    )
    return csrf_token


def clear_session_cookies(response: Response) -> None:
    """Remove every session cookie. Used by logout."""
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")


def token_from_cookie(request: Request | None) -> str | None:
    """The access token carried by this request's cookies, if any."""
    if request is None:
        return None
    try:
        return request.cookies.get(ACCESS_COOKIE)
    except Exception:
        return None


def csrf_is_valid(request: Request) -> bool:
    """Whether this request satisfies the double-submit check."""
    cookie_value = request.cookies.get(CSRF_COOKIE)
    header_value = request.headers.get(CSRF_HEADER)
    if not cookie_value or not header_value:
        return False
    return secrets.compare_digest(cookie_value, header_value)
