"""Resolving *whose* identity provider is answering.

Interactive SSO read its configuration from deployment-wide environment
variables, so one deployment offered one IdP. Domain routing (see
``denoiser.api.tenancy``) already decided which organisation a federated
identity belonged to once it arrived — but two companies could not each point
their own Okta at the same deployment for sign-in. SCIM provisioning was
already per-organisation; interactive login was the half that was not.

The hard part is not storage, it is **routing**: at the moment a login starts,
nobody is authenticated yet, so the request cannot be trusted to say which
organisation it belongs to. Each protocol gets the strongest signal available:

* **SAML, inbound.** The assertion names its own ``Issuer``, and that value is
  covered by the signature. So the ACS endpoint looks the organisation up by
  issuer and verifies against *that* organisation's certificate. No client
  hint is involved, and an attacker cannot select which certificate their
  assertion is checked against by changing a query parameter.

* **SAML and OIDC, outbound.** Starting a login needs a hint — an ``org`` or an
  email domain — because there is nothing else to go on yet. That is safe in a
  way the inbound direction would not be: the worst a forged hint achieves is
  being redirected to somebody else's IdP, which will not authenticate you.

* **OIDC, inbound.** The signed ``state`` carries the organisation chosen at
  the start, so the callback validates the token against the same provider that
  issued it rather than re-deriving it from anything the caller sends.

Deployment-wide environment configuration still works and is used whenever an
organisation has no row of its own, so single-customer installs are unaffected.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from denoiser.logging import get_logger
from denoiser.storage.db import Tenant, TenantIdentityProvider, utcnow
from denoiser.storage.secrets import decrypt, encrypt

logger = get_logger(__name__)

OIDC = "oidc"
SAML = "saml"
PROTOCOLS = (OIDC, SAML)


def get_provider(db: Session, tenant_id: int | None, protocol: str) -> TenantIdentityProvider | None:
    """This organisation's provider for one protocol, if it has one enabled."""
    if tenant_id is None:
        return None
    return (
        db.query(TenantIdentityProvider)
        .filter(
            TenantIdentityProvider.tenant_id == tenant_id,
            TenantIdentityProvider.protocol == protocol,
            TenantIdentityProvider.enabled == True,  # SQL comparison, not Python truthiness
        )
        .first()
    )


def provider_for_saml_issuer(db: Session, issuer: str | None) -> TenantIdentityProvider | None:
    """The organisation whose SAML IdP claims this issuer.

    This is the inbound routing key, and it is the right one: the issuer is
    inside the signed assertion, so it cannot be swapped by whoever posts it.
    Routing on a query parameter instead would let an attacker choose which
    organisation's certificate their assertion is verified against.
    """
    if not issuer:
        return None
    return (
        db.query(TenantIdentityProvider)
        .filter(
            TenantIdentityProvider.protocol == SAML,
            TenantIdentityProvider.saml_idp_entity_id == issuer.strip(),
            TenantIdentityProvider.enabled == True,  # SQL comparison, not Python truthiness
        )
        .first()
    )


def tenant_for_hint(db: Session, hint: str | None) -> Tenant | None:
    """Resolve an organisation from a login hint.

    Accepts an organisation name, an email domain, or a whole email address —
    the login page has one field and the user may type any of them. A hint that
    matches nothing returns None and the caller falls back to the
    deployment-wide provider, which is what a single-customer install has.
    """
    if not hint:
        return None

    from denoiser.api.tenancy import domain_of, tenant_claiming

    candidate = hint.strip().lower()
    if not candidate:
        return None

    domain = domain_of(candidate) or candidate.lstrip("@")
    tenant = tenant_claiming(db, domain)
    if tenant:
        return tenant

    # Fall back to an exact organisation name. Case-insensitive because it
    # arrives from a URL somebody typed.
    for row in db.query(Tenant).order_by(Tenant.id).all():
        if (row.name or "").strip().lower() == candidate:
            return row
    return None


# ── SAML ─────────────────────────────────────────────────────────────────────

def saml_config_for(db: Session, tenant_id: int | None) -> Any:
    """This organisation's SAMLConfig, or the deployment-wide one.

    The SP half (entity id, ACS URL) stays deployment-wide: it describes *this
    service*, which is the same service whoever is signing in. Only the IdP
    half — who we trust to assert an identity — is per organisation.
    """
    from denoiser.api.saml import SAMLConfig, get_saml_config

    fallback = get_saml_config()
    provider = get_provider(db, tenant_id, SAML)
    if provider is None:
        return fallback

    certificate = decrypt(provider.saml_idp_certificate) or ""
    return SAMLConfig(
        idp_entity_id=(provider.saml_idp_entity_id or "").strip(),
        idp_sso_url=(provider.saml_idp_sso_url or "").strip(),
        idp_certificate=certificate.strip(),
        sp_entity_id=fallback.sp_entity_id,
        sp_acs_url=fallback.sp_acs_url,
        clock_skew_seconds=fallback.clock_skew_seconds,
    )


def saml_config_for_issuer(db: Session, issuer: str | None) -> tuple[Any, int | None]:
    """``(config, tenant_id)`` for an inbound assertion, by its signed issuer."""
    provider = provider_for_saml_issuer(db, issuer)
    if provider is None:
        from denoiser.api.saml import get_saml_config

        return get_saml_config(), None
    return saml_config_for(db, provider.tenant_id), provider.tenant_id


def peek_saml_issuer(saml_response_b64: str | None) -> str | None:
    """Read the ``Issuer`` out of an unverified response, for routing only.

    This parses input nothing has authenticated yet, so the value it returns is
    a **hint and nothing more**. It is used solely to choose which
    organisation's certificate to verify against; the full verification then
    re-reads the issuer from inside the signed assertion and rejects the
    response if it does not match that certificate's configured entity id.

    So a forged issuer here cannot promote an assertion — it can only select a
    certificate that will fail to verify it. Returning None simply falls back
    to the deployment-wide configuration.
    """
    if not saml_response_b64:
        return None

    import base64

    from denoiser.api.saml import NS, _parse_xml

    try:
        raw = base64.b64decode(saml_response_b64, validate=True)
        if not raw:
            return None
        root = _parse_xml(raw)
    except Exception as exc:
        # Deliberately broad: this is best-effort routing over hostile input,
        # and every real parse failure is re-raised with a proper diagnostic by
        # the verification path immediately afterwards. Swallowing it here only
        # means "no routing hint".
        logger.debug("Could not read a SAML issuer for routing: %s", exc)
        return None

    # Prefer the assertion-level issuer, matching what verification treats as
    # authoritative, so routing and verification agree on which IdP is claimed.
    for path in ("saml:Assertion/saml:Issuer", "saml:Issuer"):
        found = root.findtext(path, default="", namespaces=NS)
        if found and found.strip():
            return found.strip()
    return None


# ── OIDC ─────────────────────────────────────────────────────────────────────

def oidc_settings_for(db: Session, tenant_id: int | None) -> dict[str, str]:
    """``{issuer, client_id, client_secret}`` for this organisation.

    Falls back to the deployment-wide environment configuration, so an install
    with a single customer needs no rows at all.
    """
    provider = get_provider(db, tenant_id, OIDC)
    if provider is None:
        from denoiser.settings import get_settings

        settings = get_settings()
        return {
            "issuer": getattr(settings, "oidc_issuer", "") or "",
            "client_id": getattr(settings, "oidc_client_id", "") or "",
            "client_secret": getattr(settings, "oidc_client_secret", "") or "",
        }

    return {
        "issuer": (provider.oidc_issuer or "").strip(),
        "client_id": (provider.oidc_client_id or "").strip(),
        "client_secret": (decrypt(provider.oidc_client_secret) or "").strip(),
    }


# ── Configuration ────────────────────────────────────────────────────────────

def upsert_provider(
    db: Session,
    tenant_id: int,
    protocol: str,
    *,
    enabled: bool = True,
    oidc_issuer: str | None = None,
    oidc_client_id: str | None = None,
    oidc_client_secret: str | None = None,
    saml_idp_entity_id: str | None = None,
    saml_idp_sso_url: str | None = None,
    saml_idp_certificate: str | None = None,
) -> TenantIdentityProvider:
    """Create or replace one organisation's provider for a protocol.

    Secrets are only overwritten when a new value is supplied, so an update
    that changes the issuer does not silently blank the client secret — which
    would take that organisation's sign-in down at the next login rather than
    at the moment of the edit.
    """
    if protocol not in PROTOCOLS:
        raise ValueError(f"unknown protocol {protocol!r}")

    provider = (
        db.query(TenantIdentityProvider)
        .filter(
            TenantIdentityProvider.tenant_id == tenant_id,
            TenantIdentityProvider.protocol == protocol,
        )
        .first()
    )
    if provider is None:
        provider = TenantIdentityProvider(tenant_id=tenant_id, protocol=protocol)
        db.add(provider)

    provider.enabled = enabled
    if protocol == OIDC:
        if oidc_issuer is not None:
            provider.oidc_issuer = oidc_issuer.strip()
        if oidc_client_id is not None:
            provider.oidc_client_id = oidc_client_id.strip()
        if oidc_client_secret:
            provider.oidc_client_secret = encrypt(oidc_client_secret.strip())
    else:
        if saml_idp_entity_id is not None:
            provider.saml_idp_entity_id = saml_idp_entity_id.strip()
        if saml_idp_sso_url is not None:
            provider.saml_idp_sso_url = saml_idp_sso_url.strip()
        if saml_idp_certificate:
            provider.saml_idp_certificate = encrypt(saml_idp_certificate.strip())

    provider.updated_at = utcnow()
    db.commit()
    db.refresh(provider)
    logger.info("Configured the %s provider for tenant %s", protocol, tenant_id)
    return provider


def describe(provider: TenantIdentityProvider) -> dict[str, Any]:
    """Safe-to-return view. Never includes a secret's value."""
    body: dict[str, Any] = {
        "protocol": provider.protocol,
        "enabled": bool(provider.enabled),
        "updated_at": provider.updated_at.isoformat() if provider.updated_at else None,
    }
    if provider.protocol == OIDC:
        body["issuer"] = provider.oidc_issuer
        body["client_id"] = provider.oidc_client_id
        body["client_secret_set"] = bool(provider.oidc_client_secret)
    else:
        body["idp_entity_id"] = provider.saml_idp_entity_id
        body["idp_sso_url"] = provider.saml_idp_sso_url
        body["certificate_set"] = bool(provider.saml_idp_certificate)
    return body
