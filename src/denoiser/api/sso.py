from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import os
import sys

from denoiser.storage.db import get_db, User, Tenant
from denoiser.api.auth import create_access_token
from denoiser.api.schemas import TokenResponse

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


def _is_safe_redirect(uri: str) -> bool:
    """Only permit same-origin relative paths to avoid open-redirect/token leak."""
    # Reject absolute URLs, scheme-relative ("//host"), and backslash tricks.
    return uri.startswith("/") and not uri.startswith("//") and "\\" not in uri


@router.get("/login")
def sso_login(provider: str = "okta", redirect_uri: str | None = None):
    """
    Initiate SSO redirection. In production, this redirects to the configured
    Okta/SAML IdP. For local development/sandbox, we redirect to our mock callback.
    """
    if not _mock_sso_enabled():
        # Production path: a real OIDC/SAML authorization URL must be built and the
        # returned assertion cryptographically verified before any token is issued.
        raise HTTPException(
            status_code=501,
            detail="SSO is not configured. Set up an OIDC/SAML IdP (mock SSO is disabled).",
        )

    target_uri = redirect_uri or "/auth/sso/callback"
    if not _is_safe_redirect(target_uri):
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")
    mock_idp_url = f"{target_uri}?code=mock_okta_code_abc123&provider={provider}"
    return RedirectResponse(url=mock_idp_url)


@router.get("/callback", response_model=TokenResponse)
def sso_callback(
    code: str = Query(..., description="SSO authorization code or SAML assertion token"),
    provider: str = "okta",
    db: Session = Depends(get_db)
):
    """
    Assertion Consumer Service (ACS) / OAuth Callback.
    Verifies the SSO assertion, resolves the user attributes (email, department, roles),
    provisions the user if not exists, and issues a standard platform JWT.
    """
    if not _mock_sso_enabled():
        # No real IdP integration is wired up yet. Refuse rather than mint a token
        # off an unverified assertion — issuing one here is a full auth bypass.
        raise HTTPException(
            status_code=501,
            detail="SSO is not configured. A real OIDC/SAML assertion verifier must be wired up.",
        )

    if "mock_okta_code" not in code:
        raise HTTPException(status_code=400, detail="Invalid or expired SSO token")

    # In production, we exchange 'code' for Okta ID token or decode the SAML assertion XML.
    # We simulate resolving user attributes from Okta/SAML:
    sso_email = "okta-operator@semanticos.io"
    sso_department = "Operations"
    sso_environments = ["prod", "staging", "dev"]
    sso_role = "ANALYST"

    # Auto-provision user if not exists
    default_tenant = db.query(Tenant).filter(Tenant.name == "Default Workspace").first()
    tenant_id = default_tenant.id if default_tenant else None

    user = db.query(User).filter(User.email == sso_email).first()
    if not user:
        # Create user
        from denoiser.api.auth import get_password_hash
        # SSO users don't use local password, we set a strong random one
        import uuid
        dummy_pwd = get_password_hash(str(uuid.uuid4()))
        user = User(
            email=sso_email,
            hashed_password=dummy_pwd,
            role=sso_role,
            tenant_id=tenant_id,
            is_active=True,
            department=sso_department,
            environment_access=sso_environments
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    if not user.is_active:
        raise HTTPException(status_code=401, detail="User account is deactivated")

    # Issue JWT
    access_token = create_access_token(data={"sub": user.email})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }


@router.post("/saml/acs", response_model=TokenResponse)
def saml_acs(db: Session = Depends(get_db)):
    """
    ACS endpoint mapping to post-back SAML XML assertions.
    """
    # SAML POST callback simulation
    return sso_callback(code="mock_okta_code_saml", provider="saml", db=db)
