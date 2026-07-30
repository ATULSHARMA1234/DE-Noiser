"""Vendor-side operations: onboarding and offboarding whole customers.

These are deliberately *not* behind the ADMIN role. An ADMIN is a customer's own
administrator; letting them create or delete organisations would let one company
manipulate the boundary that separates them from another. This router is gated
by a separate operator credential (``SEMANTICOS_PLATFORM_TOKEN``) held by
whoever runs the deployment, and is disabled entirely until that is set.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from denoiser.api.credentials import generate_api_key, secrets_match
from denoiser.api.keys import read_secret
from denoiser.api.pagination import ResourceId
from denoiser.api.tenancy import (
    conflicting_domains,
    domain_of,
    normalise_domains,
    purge_tenant,
    rotate_scim_token,
    tenant_claiming,
)
from denoiser.logging import get_logger
from denoiser.storage.db import Tenant, User, get_db
from denoiser.utils.time import iso_utc

logger = get_logger(__name__)

router = APIRouter(prefix="/platform", tags=["Platform"])

PLATFORM_TOKEN_ENV = "SEMANTICOS_PLATFORM_TOKEN"


def require_platform_operator(authorization: str | None = Header(None)) -> bool:
    """Authenticate the deployment operator. 403 while no token is configured.

    Fail-closed on purpose: an unset token means "nobody may create or destroy
    organisations", not "everybody may".
    """
    configured = read_secret(PLATFORM_TOKEN_ENV)
    if not configured:
        raise HTTPException(
            status_code=403,
            detail=f"Platform administration is disabled; set {PLATFORM_TOKEN_ENV} to enable it.",
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing platform operator token")
    presented = authorization.split(" ", 1)[1].strip()
    if not secrets_match(presented, configured):
        raise HTTPException(status_code=401, detail="Invalid platform operator token")
    return True


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # Email domains the customer owns. SSO and SCIM identities are routed by
    # these, so registering them is what turns a single-customer deployment into
    # a shared one.
    domains: list[str] = Field(default_factory=list, max_length=50)
    tier: str = Field(default="free", pattern="^(free|pro|enterprise)$")
    # The customer's first administrator. Without one the organisation exists
    # but nobody can sign in to it: there is no self-registration, and the
    # seeded admin belongs to the default tenant only. Optional because an
    # SSO-only customer may prefer their first admin to arrive from their IdP.
    # Plain `str` with a shape check rather than `EmailStr`: pydantic's email
    # type pulls in the optional `email-validator` dependency, and the only
    # property this field needs is a parseable domain to route on.
    admin_email: str | None = Field(default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UpdateTenantRequest(BaseModel):
    domains: list[str] | None = Field(default=None, max_length=50)
    tier: str | None = Field(default=None, pattern="^(free|pro|enterprise)$")


class DeleteTenantRequest(BaseModel):
    # Echoing the name back is the guard against deleting the wrong customer:
    # an id is easy to fat-finger, a name is not.
    confirm_name: str


def _public(tenant: Tenant, db: Session) -> dict:
    return {
        "id": tenant.id,
        "name": tenant.name,
        "tier": tenant.tier,
        "domains": normalise_domains(tenant.sso_domains),
        "users": db.query(User).filter(User.tenant_id == tenant.id).count(),
        "scim_token_configured": bool(tenant.scim_token),
        "created_at": iso_utc(tenant.created_at),
    }


@router.get("/tenants")
def list_tenants(_: bool = Depends(require_platform_operator), db: Session = Depends(get_db)):
    """Every organisation on this deployment. No secrets are returned."""
    return {"tenants": [_public(t, db) for t in db.query(Tenant).order_by(Tenant.id).all()]}


@router.post("/tenants", status_code=201)
def create_tenant(
    payload: CreateTenantRequest,
    _: bool = Depends(require_platform_operator),
    db: Session = Depends(get_db),
):
    """Onboard a customer, returning their credentials once.

    The API key and SCIM token are shown here and never again — they are stored
    hashed or encrypted, and rotating is the way to recover from losing them.
    """
    if db.query(Tenant).filter(Tenant.name == payload.name).first():
        raise HTTPException(status_code=409, detail="An organisation with that name already exists")

    domains = normalise_domains(payload.domains)
    taken = conflicting_domains(db, domains)
    if taken:
        raise HTTPException(
            status_code=409,
            detail=f"Already registered to another organisation: {', '.join(taken)}",
        )

    # Checked before the tenant is written, so a rejected admin address does not
    # leave a half-onboarded organisation behind.
    if payload.admin_email:
        if db.query(User).filter(User.email == payload.admin_email).first():
            raise HTTPException(status_code=409, detail="That admin email is already in use")
        owner = tenant_claiming(db, domain_of(payload.admin_email))
        if owner is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{payload.admin_email}' is on a domain registered to "
                    f"'{owner.name}'. Their first admin would be routed to that "
                    "organisation on their next SSO login."
                ),
            )

    tenant = Tenant(
        name=payload.name,
        tier=payload.tier,
        sso_domains=domains,
        api_key=generate_api_key(),
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    scim_token = rotate_scim_token(db, tenant)

    admin_password = None
    if payload.admin_email:
        admin_password = _create_first_admin(db, tenant, payload.admin_email)

    logger.info("Onboarded tenant %s (%s) with domains %s", tenant.id, tenant.name, domains)

    body = {
        **_public(tenant, db),
        "api_key": tenant.api_key,
        "scim_token": scim_token,
        "warning": "Store these credentials now — none of them is retrievable again.",
    }
    if admin_password:
        body["admin_email"] = payload.admin_email
        body["admin_password"] = admin_password
        body["next_step"] = (
            "Give the admin these credentials over a channel you trust, and have "
            "them change the password at first sign-in."
        )
    return body


def _create_first_admin(db: Session, tenant: Tenant, email: str) -> str:
    """Seed the organisation's first ADMIN with a one-time password.

    Somebody has to be able to sign in before anybody else can be invited, and
    an ADMIN cannot bootstrap themselves — `/users` requires an existing ADMIN
    of that same organisation. This is the only way into a new tenant that does
    not depend on the customer's IdP already being wired up.
    """
    from denoiser.api.auth import get_password_hash

    password = generate_api_key(prefix="tmp")
    db.add(User(
        email=email,
        hashed_password=get_password_hash(password),
        role="ADMIN",
        tenant_id=tenant.id,
        is_active=True,
        department="Operations",
        # Full environment access: the first admin has to be able to grant
        # narrower access to everyone else, which they cannot do from inside a
        # narrower grant of their own.
        environment_access=["*"],
    ))
    db.commit()
    logger.info("Seeded first ADMIN %s for tenant %s", email, tenant.id)
    return password


@router.patch("/tenants/{tenant_id}")
def update_tenant(
    tenant_id: ResourceId,
    payload: UpdateTenantRequest,
    _: bool = Depends(require_platform_operator),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organisation not found")

    if payload.domains is not None:
        domains = normalise_domains(payload.domains)
        taken = conflicting_domains(db, domains, exclude_tenant_id=tenant.id)
        if taken:
            raise HTTPException(
                status_code=409,
                detail=f"Already registered to another organisation: {', '.join(taken)}",
            )
        tenant.sso_domains = domains
    if payload.tier is not None:
        tenant.tier = payload.tier

    db.commit()
    db.refresh(tenant)
    return _public(tenant, db)


@router.post("/tenants/{tenant_id}/scim-token/rotate")
def rotate_tenant_scim_token(
    tenant_id: ResourceId,
    _: bool = Depends(require_platform_operator),
    db: Session = Depends(get_db),
):
    """Issue this organisation's SCIM bearer token. Returned once."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organisation not found")
    token = rotate_scim_token(db, tenant)
    return {
        "status": "rotated",
        "scim_token": token,
        "warning": "Store this token now — it is not retrievable again.",
    }


@router.delete("/tenants/{tenant_id}")
def delete_tenant(
    tenant_id: ResourceId,
    payload: DeleteTenantRequest,
    _: bool = Depends(require_platform_operator),
    db: Session = Depends(get_db),
):
    """Offboard a customer: delete them and every trace of their data.

    Irreversible, and it spans stores that no other code path knows how to clean
    together — relational rows, ClickHouse, embeddings, uploaded sources and
    cold archives. The response reports what was removed from each, and lists
    any store that could not be reached so the purge can be repeated rather than
    quietly leaving data behind.
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organisation not found")
    if payload.confirm_name != tenant.name:
        raise HTTPException(
            status_code=400,
            detail="confirm_name does not match the organisation's name",
        )

    report = purge_tenant(db, tenant)
    if report["errors"]:
        # Reported rather than raised: the relational delete already succeeded,
        # and a 500 here would hide both what was removed and what was not. The
        # caller must not treat "partial" as a completed erasure request.
        return {**report, "status": "partial"}
    return {**report, "status": "purged"}
