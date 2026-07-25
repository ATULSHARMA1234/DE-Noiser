"""
Real OIDC (OpenID Connect) Authorization Code flow.

Replaces the mock IdP for production: builds the authorization URL from the
provider's discovery document, exchanges the code for tokens, and cryptograph-
ically validates the ID token against the provider's JWKS before any platform
token is issued. Group claims are mapped to a SemanticOS role and team list so a
large workforce is provisioned automatically at first login.

CSRF is handled with a short-lived signed ``state`` (HS256 over the platform JWT
secret), so no server-side session store is required.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from denoiser.api.auth import ALGORITHM, SECRET_KEY
from denoiser.logging import get_logger
from denoiser.settings import get_settings

logger = get_logger(__name__)

_STATE_TTL_SECONDS = 600
# How long a cached JWKS is trusted before we re-fetch. Providers rotate signing
# keys periodically; without an expiry the process would serve stale keys until
# restart and reject every freshly-signed token (a self-inflicted auth outage).
_JWKS_TTL_SECONDS = 600
# Cache of issuer -> discovery document, and jwks_uri -> (keys, fetched_at).
_discovery_cache: dict[str, dict[str, Any]] = {}
_jwks_cache: dict[str, tuple[dict[str, Any], float]] = {}


class OIDCError(Exception):
    """Any failure in the OIDC flow — surfaced as a 4xx, never a token."""


def _http() -> httpx.Client:
    return httpx.Client(timeout=10)


def discover(issuer: str) -> dict[str, Any]:
    """Fetch (and cache) the provider's OpenID discovery document."""
    if issuer in _discovery_cache:
        return _discovery_cache[issuer]
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    with _http() as client:
        resp = client.get(url)
        resp.raise_for_status()
        doc = resp.json()
    _discovery_cache[issuer] = doc
    return doc


def _jwks(jwks_uri: str, *, force: bool = False) -> dict[str, Any]:
    """Fetch (and cache with a TTL) the provider JWKS.

    ``force=True`` bypasses the cache — used when a token's ``kid`` is missing
    from the cached set, which is the signal that the provider has rotated keys.
    """
    cached = _jwks_cache.get(jwks_uri)
    if not force and cached is not None and (time.time() - cached[1]) < _JWKS_TTL_SECONDS:
        return cached[0]
    with _http() as client:
        resp = client.get(jwks_uri)
        resp.raise_for_status()
        keys = resp.json()
    _jwks_cache[jwks_uri] = (keys, time.time())
    return keys


def issue_state(redirect_uri: str) -> str:
    """A signed, short-lived CSRF state carrying the post-login redirect."""
    payload = {
        "redirect": redirect_uri,
        "exp": datetime.now(UTC) + timedelta(seconds=_STATE_TTL_SECONDS),
        "purpose": "oidc_state",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_state(state: str) -> str:
    """Validate a state token and return its redirect. Raises on tamper/expiry."""
    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise OIDCError(f"Invalid or expired state: {e}")
    if payload.get("purpose") != "oidc_state":
        raise OIDCError("State token has the wrong purpose")
    return payload.get("redirect") or "/"


def build_authorization_url(redirect_uri: str) -> str:
    """Construct the provider authorization URL for the Authorization Code flow."""
    s = get_settings()
    if not s.oidc_enabled:
        raise OIDCError("OIDC is not configured")
    doc = discover(s.oidc_issuer)  # type: ignore[arg-type]
    params = {
        "response_type": "code",
        "client_id": s.oidc_client_id,
        "redirect_uri": s.oidc_redirect_uri or redirect_uri,
        "scope": s.oidc_scopes,
        "state": issue_state(redirect_uri),
    }
    return f"{doc['authorization_endpoint']}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange an authorization code for the provider's token response."""
    s = get_settings()
    doc = discover(s.oidc_issuer)  # type: ignore[arg-type]
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": s.oidc_redirect_uri or redirect_uri,
        "client_id": s.oidc_client_id,
        "client_secret": s.oidc_client_secret,
    }
    with _http() as client:
        resp = client.post(doc["token_endpoint"], data=data)
    if resp.status_code != 200:
        raise OIDCError(f"Token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()


def validate_id_token(id_token: str) -> dict[str, Any]:
    """Cryptographically validate an ID token against the provider JWKS."""
    s = get_settings()
    doc = discover(s.oidc_issuer)  # type: ignore[arg-type]
    jwks = _jwks(doc["jwks_uri"])

    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError as e:
        raise OIDCError(f"Malformed ID token: {e}")

    kid = header.get("kid")

    def _find_key(jwks_doc: dict[str, Any]) -> dict[str, Any] | None:
        keys = jwks_doc.get("keys", [])
        # Match the token's kid exactly. Only fall back to a sole key when the
        # token omits kid entirely (some providers issue single-key JWKS without
        # a kid). Never pick an arbitrary key when a specific kid was requested.
        match = next((k for k in keys if k.get("kid") == kid), None)
        if match is None and kid is None and len(keys) == 1:
            return keys[0]
        return match

    key = _find_key(jwks)
    if key is None:
        # kid not in the cached set → provider likely rotated keys. Force one
        # refresh and retry before giving up, so rotation doesn't lock users out.
        jwks = _jwks(doc["jwks_uri"], force=True)
        key = _find_key(jwks)
    if key is None:
        raise OIDCError("No matching signing key in provider JWKS for token kid")

    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=[header.get("alg", "RS256")],
            audience=s.oidc_client_id,
            issuer=s.oidc_issuer,
            options={"verify_at_hash": False},
        )
    except JWTError as e:
        raise OIDCError(f"ID token validation failed: {e}")

    if claims.get("exp", 0) < time.time():
        raise OIDCError("ID token is expired")
    return claims


def _extract_groups(claims: dict[str, Any]) -> list[str]:
    groups = claims.get("groups") or claims.get("roles") or []
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.split(",") if g.strip()]
    return [str(g) for g in groups]


def map_claims(claims: dict[str, Any]) -> dict[str, Any]:
    """Map validated ID-token claims to SemanticOS user fields.

    Group membership decides the role (admin/analyst group → elevated), and all
    groups become the user's team list.
    """
    s = get_settings()
    email = claims.get("email") or claims.get("preferred_username")
    if not email:
        raise OIDCError("ID token has no email/preferred_username claim")

    groups = _extract_groups(claims)
    lowered = {g.lower() for g in groups}
    if s.oidc_admin_group.lower() in lowered:
        role = "ADMIN"
    elif s.oidc_analyst_group.lower() in lowered:
        role = "ANALYST"
    else:
        role = "VIEWER"

    return {
        "external_id": claims.get("sub"),
        "email": email,
        "name": claims.get("name", email),
        "role": role,
        "teams": groups,
    }
