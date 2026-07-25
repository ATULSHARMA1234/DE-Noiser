import os
import sys

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from denoiser.api.auth import create_access_token
from denoiser.api.schemas import TokenResponse
from denoiser.storage.db import Tenant, User, get_db

router = APIRouter(prefix="/auth/sso", tags=["SSO"])


def _mock_sso_enabled() -> bool:
    """
    The built-in mock IdP must NEVER be reachable in production: it issues real
    platform JWTs off a static code with no assertion verification. It is enabled
    only under pytest, or when an operator explicitly opts in via SSO_ALLOW_MOCK
    for local/sandbox use.
    """
    if "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ:
        return True
    return os.getenv("SSO_ALLOW_MOCK", "false").lower() in ("1", "true", "yes")


def _allowed_redirect_origins() -> list[str]:
    """Origins the SSO flow may redirect back to (reuses the CORS allowlist)."""
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def _is_safe_redirect(uri: str) -> bool:
    """
    Prevent open-redirect / token leak. Permit either a same-origin relative path,
    or an absolute URL whose origin is on the configured allowlist (the frontend
    legitimately passes its own origin when API and UI are on different ports).
    """
    if "\\" in uri:
        return False
    # Relative path (but not scheme-relative "//host").
    if uri.startswith("/") and not uri.startswith("//"):
        return True
    # Absolute URL: allow only if its scheme://host[:port] is allowlisted.
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    if not parsed.scheme or not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return origin in _allowed_redirect_origins()


def _provision_sso_user(db: Session, fields: dict, default_environments: list[str] | None = None) -> User:
    """Just-in-time provision/update an SSO user from mapped IdP claims.

    Matches on the IdP subject (``external_id``) first, then email, so a user
    whose email changes at the IdP is still recognised. Role and team membership
    are refreshed on every login, so IdP group changes take effect immediately.
    """
    from denoiser.api.auth import get_password_hash

    default_tenant = db.query(Tenant).order_by(Tenant.id).first()
    tenant_id = default_tenant.id if default_tenant else None

    user = None
    if fields.get("external_id"):
        user = db.query(User).filter(User.external_id == fields["external_id"]).first()
    if user is None:
        user = db.query(User).filter(User.email == fields["email"]).first()

    if user is None:
        import uuid
        user = User(
            email=fields["email"],
            hashed_password=get_password_hash(str(uuid.uuid4())),  # SSO users have no local password
            role=fields.get("role", "VIEWER"),
            tenant_id=tenant_id,
            is_active=True,
            department=fields.get("department", "Engineering"),
            environment_access=default_environments or ["dev"],
            external_id=fields.get("external_id"),
            teams=fields.get("teams", []),
        )
        db.add(user)
    else:
        # Refresh federated attributes on each login.
        user.role = fields.get("role", user.role)
        user.teams = fields.get("teams", user.teams)
        if fields.get("external_id"):
            user.external_id = fields["external_id"]
        user.is_active = True

    db.commit()
    db.refresh(user)
    return user


@router.get("/login")
def sso_login(provider: str = "okta", redirect_uri: str | None = None):
    """
    Initiate SSO redirection. When OIDC is configured, build the real provider
    authorization URL; otherwise fall back to the mock IdP (dev/sandbox only).
    """
    from denoiser.api.oidc import OIDCError, build_authorization_url
    from denoiser.settings import get_settings

    target_uri = redirect_uri or "/auth/sso/callback"
    if not _is_safe_redirect(target_uri):
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")

    if get_settings().oidc_enabled:
        try:
            return RedirectResponse(url=build_authorization_url(target_uri))
        except OIDCError as e:
            raise HTTPException(status_code=502, detail=f"OIDC provider error: {e}")

    if not _mock_sso_enabled():
        raise HTTPException(
            status_code=501,
            detail="SSO is not configured. Set OIDC_ISSUER/OIDC_CLIENT_ID/OIDC_CLIENT_SECRET (mock SSO is disabled).",
        )

    mock_idp_url = f"{target_uri}?code=mock_okta_code_abc123&provider={provider}"
    return RedirectResponse(url=mock_idp_url)


@router.get("/callback", response_model=TokenResponse)
def sso_callback(
    code: str = Query(..., description="SSO authorization code or SAML assertion token"),
    provider: str = "okta",
    state: str | None = Query(None, description="OIDC CSRF state"),
    db: Session = Depends(get_db)
):
    """
    OAuth/OIDC callback. Exchanges the code, cryptographically validates the ID
    token, maps its claims to a role + teams, provisions/updates the user, and
    issues a platform JWT. Falls back to the mock IdP only when OIDC is unset.
    """
    from denoiser.settings import get_settings

    if get_settings().oidc_enabled:
        from denoiser.api.oidc import OIDCError, exchange_code, map_claims, validate_id_token

        try:
            # Validate CSRF state when present (the mock flow doesn't send one).
            if state:
                from denoiser.api.oidc import verify_state
                verify_state(state)
            tokens = exchange_code(code, redirect_uri="/auth/sso/callback")
            id_token = tokens.get("id_token")
            if not id_token:
                raise OIDCError("Provider response had no id_token")
            claims = validate_id_token(id_token)
            fields = map_claims(claims)
        except OIDCError as e:
            raise HTTPException(status_code=401, detail=f"SSO authentication failed: {e}")

        user = _provision_sso_user(db, fields)
        if not user.is_active:
            raise HTTPException(status_code=401, detail="User account is deactivated")
        return {"access_token": create_access_token(data={"sub": user.email}), "token_type": "bearer", "user": user}

    # ── Mock IdP fallback (dev/sandbox only) ─────────────────────────────
    if not _mock_sso_enabled():
        raise HTTPException(
            status_code=501,
            detail="SSO is not configured. Set OIDC_ISSUER/OIDC_CLIENT_ID/OIDC_CLIENT_SECRET.",
        )
    if "mock_okta_code" not in code:
        raise HTTPException(status_code=400, detail="Invalid or expired SSO token")

    user = _provision_sso_user(
        db,
        {
            "external_id": "mock-okta|okta-operator",
            "email": "okta-operator@semanticos.io",
            "role": "ANALYST",
            "department": "Operations",
            "teams": ["operations"],
        },
        default_environments=["prod", "staging", "dev"],
    )
    if not user.is_active:
        raise HTTPException(status_code=401, detail="User account is deactivated")
    return {"access_token": create_access_token(data={"sub": user.email}), "token_type": "bearer", "user": user}


@router.post("/saml/acs", response_model=TokenResponse)
def saml_acs(db: Session = Depends(get_db)):
    """
    ACS endpoint mapping to post-back SAML XML assertions.
    """
    # SAML POST callback simulation
    return sso_callback(code="mock_okta_code_saml", provider="saml", db=db)
