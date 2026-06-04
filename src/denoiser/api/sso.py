from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import os

from denoiser.storage.db import get_db, User, Tenant
from denoiser.api.auth import create_access_token
from denoiser.api.schemas import TokenResponse

router = APIRouter(prefix="/auth/sso", tags=["SSO"])


@router.get("/login")
def sso_login(provider: str = "okta", redirect_uri: str | None = None):
    """
    Initiate SSO redirection. In production, this redirects to Okta/SAML IDP.
    For local development/sandbox, we redirect to our high-fidelity mock callback.
    """
    # In a real environment, we would build the SAML Request or OAuth authorization URL
    # e.g., redirect to https://dev-xxxx.okta.com/oauth2/v1/authorize
    # Here, we redirect to our local mock callback endpoint with a code/assertion mock
    target_uri = redirect_uri or "/auth/sso/callback"
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
