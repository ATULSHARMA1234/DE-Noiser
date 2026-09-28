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
from denoiser.api.idp_registry import PROTOCOLS, describe, upsert_provider
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
from denoiser.storage.db import ErasureRecord, Tenant, TenantIdentityProvider, User, get_db
from denoiser.utils.time import iso_utc, utcnow

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
    #
    # There is no longer a check that the address is unused deployment-wide: an
    # address belongs to a person per organisation, and the new organisation has
    # no users yet, so nothing it could collide with exists. Onboarding a
    # customer whose first admin already consults for another customer used to
    # fail here for no reason that survived examination.
    if payload.admin_email:
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

    tenant_name = tenant.name
    report = purge_tenant(db, tenant)

    # Record the erasure so it can be certified later. This outlives the tenant
    # on purpose: everything else about them is gone, and the ClickHouse
    # deletes are only *submitted* at this point — the mutations that actually
    # rewrite the parts finish minutes or hours later on a large table. Issuing
    # a certificate against this response would be certifying a queued request.
    record = ErasureRecord(
        purged_tenant_id=tenant_id,
        tenant_name=tenant_name,
        clickhouse_mutations=report.get("clickhouse_mutations", []),
        report=report,
    )
    try:
        db.add(record)
        db.commit()
        db.refresh(record)
        erasure_id = record.id
    except Exception as e:
        db.rollback()
        logger.error("Could not record the erasure for tenant %s: %s", tenant_id, e)
        erasure_id = None

    response = {
        **report,
        "erasure_id": erasure_id,
        # Named so nobody reads a 200 here as "the data is gone".
        "erasure_status": "submitted",
        "certificate_url": f"/platform/erasures/{erasure_id}" if erasure_id else None,
    }
    if report["errors"]:
        # Reported rather than raised: the relational delete already succeeded,
        # and a 500 here would hide both what was removed and what was not. The
        # caller must not treat "partial" as a completed erasure request.
        return {**response, "status": "partial"}
    return {**response, "status": "purged"}


class IdentityProviderRequest(BaseModel):
    """One organisation's IdP. Only the fields for `protocol` are read."""

    protocol: str = Field(..., description="oidc or saml")
    enabled: bool = True

    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None

    saml_idp_entity_id: str | None = None
    saml_idp_sso_url: str | None = None
    saml_idp_certificate: str | None = None


@router.put("/tenants/{tenant_id}/idp")
def configure_identity_provider(
    tenant_id: ResourceId,
    payload: IdentityProviderRequest,
    _: bool = Depends(require_platform_operator),
    db: Session = Depends(get_db),
):
    """Give one organisation its own identity provider.

    Platform-operator gated rather than ADMIN: whoever controls an
    organisation's IdP configuration controls who can sign in as their staff,
    and — through the SAML issuer, which is the inbound routing key — could
    claim assertions intended for a different customer. That is a boundary a
    customer's own administrator must not be able to move.

    Secrets are write-only. Supplying no client secret on an update leaves the
    stored one in place, so editing an issuer does not silently break sign-in
    at the next login rather than at the moment of the edit.
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organisation not found")

    protocol = payload.protocol.strip().lower()
    if protocol not in PROTOCOLS:
        raise HTTPException(
            status_code=400, detail=f"protocol must be one of {', '.join(PROTOCOLS)}"
        )

    if protocol == "saml" and payload.saml_idp_entity_id:
        # An issuer identifies exactly one organisation, because it is what an
        # inbound assertion is routed by. Two organisations claiming the same
        # issuer would make that routing ambiguous, and the loser's staff would
        # be seated inside the winner's data.
        clash = (
            db.query(TenantIdentityProvider)
            .filter(
                TenantIdentityProvider.protocol == "saml",
                TenantIdentityProvider.saml_idp_entity_id == payload.saml_idp_entity_id.strip(),
                TenantIdentityProvider.tenant_id != tenant_id,
            )
            .first()
        )
        if clash:
            raise HTTPException(
                status_code=409,
                detail=(
                    "That SAML issuer is already registered to another organisation. "
                    "An issuer identifies exactly one customer."
                ),
            )

    try:
        provider = upsert_provider(
            db,
            tenant_id,
            protocol,
            enabled=payload.enabled,
            oidc_issuer=payload.oidc_issuer,
            oidc_client_id=payload.oidc_client_id,
            oidc_client_secret=payload.oidc_client_secret,
            saml_idp_entity_id=payload.saml_idp_entity_id,
            saml_idp_sso_url=payload.saml_idp_sso_url,
            saml_idp_certificate=payload.saml_idp_certificate,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "configured", "tenant_id": tenant_id, **describe(provider)}


@router.get("/tenants/{tenant_id}/idp")
def list_identity_providers(
    tenant_id: ResourceId,
    _: bool = Depends(require_platform_operator),
    db: Session = Depends(get_db),
):
    """This organisation's providers. Never returns a secret's value."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organisation not found")

    providers = (
        db.query(TenantIdentityProvider)
        .filter(TenantIdentityProvider.tenant_id == tenant_id)
        .all()
    )
    return {
        "tenant_id": tenant_id,
        "providers": [describe(p) for p in providers],
        # Says plainly what happens when the list is empty, rather than leaving
        # an operator to infer that sign-in is broken.
        "fallback": (
            "This organisation has no provider of its own; sign-in uses the "
            "deployment-wide OIDC/SAML environment configuration."
            if not providers
            else None
        ),
    }


@router.delete("/tenants/{tenant_id}/idp/{protocol}")
def remove_identity_provider(
    tenant_id: ResourceId,
    protocol: str,
    _: bool = Depends(require_platform_operator),
    db: Session = Depends(get_db),
):
    """Remove a provider. The organisation falls back to the deployment-wide one."""
    provider = (
        db.query(TenantIdentityProvider)
        .filter(
            TenantIdentityProvider.tenant_id == tenant_id,
            TenantIdentityProvider.protocol == protocol.strip().lower(),
        )
        .first()
    )
    if not provider:
        raise HTTPException(status_code=404, detail="No such provider for this organisation")

    db.delete(provider)
    db.commit()
    return {"status": "removed", "tenant_id": tenant_id, "protocol": protocol}


@router.get("/erasures")
def list_erasures(
    _: bool = Depends(require_platform_operator),
    db: Session = Depends(get_db),
):
    """Every offboarding this deployment has performed, newest first."""
    records = (
        db.query(ErasureRecord).order_by(ErasureRecord.requested_at.desc()).limit(200).all()
    )
    return {
        "erasures": [
            {
                "id": r.id,
                "tenant_id": r.purged_tenant_id,
                "tenant_name": r.tenant_name,
                "requested_at": r.requested_at.isoformat() if r.requested_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "complete": r.completed_at is not None,
            }
            for r in records
        ]
    }


@router.get("/erasures/{erasure_id}")
def get_erasure(
    erasure_id: ResourceId,
    _: bool = Depends(require_platform_operator),
    db: Session = Depends(get_db),
):
    """Whether an offboarding has actually finished, and the evidence for it.

    Checks the ClickHouse mutations rather than trusting that the purge
    endpoint returned 200. Until every one reports `is_done`, the erasure is
    *submitted* — the customer's log rows are still on disk in parts that have
    not been rewritten yet, and a certificate issued now would be wrong.

    Completion is recorded once and then trusted: mutations leave
    `system.mutations` after they are applied, so re-deriving the answer
    forever would eventually turn a finished erasure back into an unknown one.
    """
    record = db.query(ErasureRecord).filter(ErasureRecord.id == erasure_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Erasure record not found")

    body: dict = {
        "id": record.id,
        "tenant_id": record.purged_tenant_id,
        "tenant_name": record.tenant_name,
        "requested_at": record.requested_at.isoformat() if record.requested_at else None,
        "stores": record.report.get("deleted", {}) if record.report else {},
        "errors": record.report.get("errors", []) if record.report else [],
        "warnings": record.report.get("warnings", []) if record.report else [],
    }

    if record.completed_at:
        return {
            **body,
            "status": "complete",
            "completed_at": record.completed_at.isoformat(),
            "certificate": (
                f"All data belonging to organisation {record.tenant_name} "
                f"(id {record.purged_tenant_id}) was erased from every store by "
                f"{record.completed_at.isoformat()}."
            ),
        }

    from denoiser import runtime

    status = runtime.clickhouse_store().mutation_status(
        list(record.clickhouse_mutations or [])
    )
    body["mutations"] = status.get("mutations", [])

    if status.get("error"):
        return {**body, "status": "unverified", "detail": status["error"]}

    if not status.get("complete"):
        return {
            **body,
            "status": "submitted",
            "pending_mutations": status.get("pending", 0),
            "detail": (
                "The deletion has been accepted but ClickHouse has not finished "
                "rewriting the affected parts. No erasure certificate should be "
                "issued until this reports complete."
            ),
        }

    record.completed_at = utcnow()
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Could not mark erasure %s complete: %s", erasure_id, e)

    return {
        **body,
        "status": "complete",
        "completed_at": record.completed_at.isoformat(),
        "certificate": (
            f"All data belonging to organisation {record.tenant_name} "
            f"(id {record.purged_tenant_id}) was erased from every store by "
            f"{record.completed_at.isoformat()}."
        ),
    }
