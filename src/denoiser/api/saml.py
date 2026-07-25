"""SAML 2.0 Web Browser SSO — service provider side.

The ACS endpoint used to be a stub that minted a session from whatever was
posted to it. This module replaces that with an actual SAML implementation:
the response is parsed, its XML signature is verified against the IdP's
certificate, and *only the signed subtree* is trusted for anything downstream.

The checks below are the ones that separate a real SP from a stub, and each
exists because skipping it is a known, exploited bypass:

  - **Signature.** Unsigned, or signed by any key other than the configured
    IdP certificate, is rejected. There is no "accept unsigned in a pinch".
  - **Signature wrapping (XSW).** Attributes are read from the subtree signxml
    reports as covered by the signature, never from the raw document, so an
    attacker cannot bolt an unsigned assertion alongside a signed one.
  - **Audience.** The assertion must be addressed to this SP's entity id, so a
    genuine assertion issued for another service cannot be replayed here.
  - **Issuer.** Must match the configured IdP entity id.
  - **Destination / Recipient.** Must match this SP's ACS URL when present.
  - **Validity window.** NotBefore / NotOnOrAfter on both the Conditions and
    the subject confirmation, with a small configurable clock skew.
  - **Replay.** An assertion id is single-use for the lifetime of its validity
    window; a second POST of the same assertion is refused.

Encrypted assertions (``EncryptedAssertion``) are *not* supported and are
rejected explicitly rather than silently ignored — an SP that skips over the
part it cannot read is an SP that authenticates nobody in particular.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
import threading
import time
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from denoiser.logging import get_logger

logger = get_logger(__name__)

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
}

STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
NAMEID_EMAIL = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
BINDING_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
BINDING_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"

# Attribute names IdPs commonly use for the same underlying field. Okta, Entra
# ID, Ping and Google all differ here, so each is checked in order.
EMAIL_ATTRIBUTES = (
    "email",
    "emailaddress",
    "mail",
    "urn:oid:0.9.2342.19200300.100.1.3",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
)
GROUP_ATTRIBUTES = (
    "groups",
    "group",
    "memberof",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
    "http://schemas.xmlsoap.org/claims/Group",
)
DEPARTMENT_ATTRIBUTES = (
    "department",
    "urn:oid:2.5.4.11",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/department",
)


class SAMLError(Exception):
    """A SAML response that must not be turned into a session."""


@dataclass(frozen=True)
class SAMLConfig:
    """Everything needed to trust an IdP and to describe ourselves to it."""

    idp_entity_id: str
    idp_sso_url: str
    idp_certificate: str
    sp_entity_id: str
    sp_acs_url: str
    clock_skew_seconds: int = 60

    @property
    def enabled(self) -> bool:
        return bool(
            self.idp_entity_id and self.idp_sso_url and self.idp_certificate
            and self.sp_entity_id and self.sp_acs_url
        )


def get_saml_config() -> SAMLConfig:
    """Read SAML configuration from the environment (``*_FILE`` supported)."""
    from denoiser.api.keys import read_secret

    return SAMLConfig(
        idp_entity_id=os.getenv("SAML_IDP_ENTITY_ID", "").strip(),
        idp_sso_url=os.getenv("SAML_IDP_SSO_URL", "").strip(),
        idp_certificate=(read_secret("SAML_IDP_X509_CERT") or "").strip(),
        sp_entity_id=os.getenv("SAML_SP_ENTITY_ID", "").strip(),
        sp_acs_url=os.getenv("SAML_SP_ACS_URL", "").strip(),
        clock_skew_seconds=int(os.getenv("SAML_CLOCK_SKEW_SECONDS", "60")),
    )


def saml_enabled() -> bool:
    return get_saml_config().enabled


# ── Replay protection ───────────────────────────────────────────────────────

class _AssertionReplayGuard:
    """Single-use assertion ids, for as long as the assertion stays valid.

    Process-local: with several replicas an assertion could in principle be
    replayed once per replica inside its (short) validity window. Redis-backed
    storage would close that; the window is minutes and the id must still pass
    every other check, so the local guard is the meaningful 90%.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def claim(self, assertion_id: str, expires_at: float) -> bool:
        """Register an id. Returns False if it was already used."""
        now = time.time()
        with self._lock:
            if len(self._seen) > 10_000:
                self._seen = {k: v for k, v in self._seen.items() if v > now}
            if self._seen.get(assertion_id, 0) > now:
                return False
            self._seen[assertion_id] = expires_at
            return True

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()


_replay_guard = _AssertionReplayGuard()


def reset_replay_guard() -> None:
    """For tests: forget which assertion ids have been consumed."""
    _replay_guard.reset()


# ── Request side (SP-initiated login) ───────────────────────────────────────

def build_authn_request(relay_state: str | None = None) -> tuple[str, str]:
    """Build a redirect URL for an ``AuthnRequest``. Returns ``(url, request_id)``.

    HTTP-Redirect binding: deflate, base64, urlencode. The request is not
    signed — signing is optional in SAML 2.0 and IdPs that require it are
    configured with our metadata instead; the response signature is what
    actually secures the flow.
    """
    config = get_saml_config()
    if not config.enabled:
        raise SAMLError("SAML is not configured")

    request_id = f"_{secrets.token_hex(16)}"
    issued = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    authn_request = (
        f'<samlp:AuthnRequest xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" '
        f'ID="{request_id}" Version="2.0" IssueInstant="{issued}" '
        f'Destination="{config.idp_sso_url}" '
        f'ProtocolBinding="{BINDING_POST}" '
        f'AssertionConsumerServiceURL="{config.sp_acs_url}">'
        f'<saml:Issuer>{config.sp_entity_id}</saml:Issuer>'
        f'</samlp:AuthnRequest>'
    )
    # HTTP-Redirect carries the *raw deflate* stream, without the zlib header.
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    deflated = compressor.compress(authn_request.encode("utf-8")) + compressor.flush()
    query = {"SAMLRequest": base64.b64encode(deflated).decode("ascii")}
    if relay_state:
        query["RelayState"] = relay_state
    separator = "&" if "?" in config.idp_sso_url else "?"
    return f"{config.idp_sso_url}{separator}{urlencode(query)}", request_id


def build_sp_metadata() -> str:
    """SP metadata XML — what an IdP administrator needs to register us."""
    config = get_saml_config()
    if not config.sp_entity_id or not config.sp_acs_url:
        raise SAMLError("SAML_SP_ENTITY_ID and SAML_SP_ACS_URL must be set")
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<md:EntityDescriptor xmlns:md="{NS["md"]}" entityID="{config.sp_entity_id}">'
        f'<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        f'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        f'<md:NameIDFormat>{NAMEID_EMAIL}</md:NameIDFormat>'
        f'<md:AssertionConsumerService Binding="{BINDING_POST}" '
        f'Location="{config.sp_acs_url}" index="0" isDefault="true"/>'
        f'</md:SPSSODescriptor></md:EntityDescriptor>'
    )


# ── Response side (the part that must not be forgeable) ─────────────────────

def _parse_xml(raw: bytes):
    """Parse untrusted XML with entity expansion and network access disabled."""
    from lxml import etree

    parser = etree.XMLParser(
        resolve_entities=False,   # no XXE
        no_network=True,          # no external DTD/entity fetches
        huge_tree=False,          # bounded memory on hostile input
        remove_comments=True,     # comments split text nodes → NameID truncation
    )
    try:
        root = etree.fromstring(raw, parser=parser)
    except etree.XMLSyntaxError as e:
        raise SAMLError(f"SAMLResponse is not well-formed XML: {e}")
    if root is None:
        raise SAMLError("SAMLResponse is empty")
    # A DOCTYPE is never legitimate here and is the vector for entity attacks.
    if root.getroottree().docinfo.doctype:
        raise SAMLError("SAMLResponse contains a DOCTYPE, which is not permitted")
    return root


def _parse_instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise SAMLError(f"Malformed SAML timestamp: {value}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _verify_signature(root, certificate: str):
    """Return the signed subtree, or raise. Never returns unverified XML."""
    from lxml import etree
    from signxml import XMLVerifier
    from signxml.verifier import SignatureConfiguration

    if root.find(".//ds:Signature", NS) is None:
        raise SAMLError("SAML response carries no signature")

    verifier = XMLVerifier()
    # A signature may cover the whole Response or just the Assertion; both are
    # valid deployments, so each candidate root is tried and the *verified*
    # subtree is what we keep.
    candidates = [root, *root.findall("saml:Assertion", NS)]
    last_error: Exception | None = None
    for candidate in candidates:
        if candidate.find("./ds:Signature", NS) is None:
            continue
        try:
            result = verifier.verify(
                etree.tostring(candidate),
                x509_cert=certificate,
                expect_config=SignatureConfiguration(
                    require_x509=True,
                    # The signature must be a direct child of the element it
                    # covers — not somewhere else in the document, which is how
                    # wrapping attacks smuggle a signature past a lax check.
                    location="./",
                    expect_references=1,
                ),
            )
        except Exception as e:  # signxml raises a family of validation errors
            last_error = e
            continue
        signed = result[0].signed_xml if isinstance(result, list) else result.signed_xml
        if signed is None:
            last_error = SAMLError("Signature verified but covered no element")
            continue
        return signed

    raise SAMLError(f"SAML signature verification failed: {last_error}")


def _assertion_from(signed_xml, original_root):
    """The assertion covered by the signature — never one merely adjacent to it.

    If the signature covers the Response, the assertion inside *that verified
    copy* is used. If it covers an Assertion directly, that is the assertion.
    Anything else (a signature over some other element) is refused, which is
    what defeats signature-wrapping.
    """
    tag = signed_xml.tag
    if tag == f"{{{NS['saml']}}}Assertion":
        return signed_xml
    if tag == f"{{{NS['samlp']}}}Response":
        assertions = signed_xml.findall("saml:Assertion", NS)
        if len(assertions) != 1:
            raise SAMLError(
                f"Signed response must contain exactly one assertion, found {len(assertions)}"
            )
        # An attacker may not add extra assertions outside the signed subtree.
        if len(original_root.findall("saml:Assertion", NS)) != 1:
            raise SAMLError("Response contains assertions outside the signature")
        return assertions[0]
    raise SAMLError(f"Signature covers an unexpected element: {tag}")


def _check_status(root) -> None:
    status = root.find("samlp:Status/samlp:StatusCode", NS)
    if status is None:
        raise SAMLError("SAML response has no status code")
    code = status.get("Value")
    if code != STATUS_SUCCESS:
        message = root.findtext("samlp:Status/samlp:StatusMessage", default="", namespaces=NS)
        raise SAMLError(f"IdP rejected the authentication: {code} {message}".strip())


def _check_conditions(assertion, config: SAMLConfig, now: datetime) -> datetime:
    """Validate audience and validity window. Returns the assertion's expiry."""
    skew = timedelta(seconds=config.clock_skew_seconds)
    conditions = assertion.find("saml:Conditions", NS)
    if conditions is None:
        raise SAMLError("Assertion has no Conditions element")

    not_before = _parse_instant(conditions.get("NotBefore"))
    not_on_or_after = _parse_instant(conditions.get("NotOnOrAfter"))
    if not_before and now + skew < not_before:
        raise SAMLError("Assertion is not yet valid")
    if not_on_or_after and now - skew >= not_on_or_after:
        raise SAMLError("Assertion has expired")

    audiences = [
        (a.text or "").strip()
        for a in conditions.findall("saml:AudienceRestriction/saml:Audience", NS)
    ]
    if not audiences:
        raise SAMLError("Assertion has no AudienceRestriction")
    if config.sp_entity_id not in audiences:
        raise SAMLError(
            f"Assertion is addressed to {audiences}, not to this service provider"
        )
    return not_on_or_after or (now + timedelta(minutes=5))


def _check_subject(assertion, config: SAMLConfig, now: datetime, request_id: str | None) -> None:
    skew = timedelta(seconds=config.clock_skew_seconds)
    confirmations = assertion.findall(
        "saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData", NS
    )
    if not confirmations:
        # Not every IdP emits confirmation data; the Conditions window and the
        # audience check still bound the assertion.
        return
    for data in confirmations:
        expires = _parse_instant(data.get("NotOnOrAfter"))
        if expires and now - skew >= expires:
            raise SAMLError("Subject confirmation has expired")
        recipient = (data.get("Recipient") or "").strip()
        if recipient and recipient.rstrip("/") != config.sp_acs_url.rstrip("/"):
            raise SAMLError(f"Assertion recipient {recipient} is not this ACS endpoint")
        in_response_to = data.get("InResponseTo")
        if request_id and in_response_to and in_response_to != request_id:
            raise SAMLError("Assertion answers a different authentication request")


def _attribute_values(assertion) -> dict[str, list[str]]:
    """All attributes, keyed by lower-cased Name and FriendlyName."""
    collected: dict[str, list[str]] = {}
    for attribute in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
        values = [
            (v.text or "").strip()
            for v in attribute.findall("saml:AttributeValue", NS)
            if (v.text or "").strip()
        ]
        if not values:
            continue
        for key in (attribute.get("Name"), attribute.get("FriendlyName")):
            if key:
                collected.setdefault(key.strip().lower(), []).extend(values)
    return collected


def _first(attributes: dict[str, list[str]], names: tuple[str, ...]) -> str | None:
    for name in names:
        values = attributes.get(name.lower())
        if values:
            return values[0]
    return None


def _all(attributes: dict[str, list[str]], names: tuple[str, ...]) -> list[str]:
    for name in names:
        values = attributes.get(name.lower())
        if values:
            return values
    return []


def map_assertion(assertion) -> dict:
    """Map a *verified* assertion to SemanticOS user fields.

    Group membership decides the role, reusing the same group names as OIDC so
    an operator configures the mapping once regardless of protocol.
    """
    from denoiser.settings import get_settings

    settings = get_settings()
    attributes = _attribute_values(assertion)

    name_id_element = assertion.find("saml:Subject/saml:NameID", NS)
    name_id = (name_id_element.text or "").strip() if name_id_element is not None else ""

    email = _first(attributes, EMAIL_ATTRIBUTES)
    if not email and "@" in name_id:
        email = name_id
    if not email:
        raise SAMLError("Assertion carries no email attribute and no email NameID")

    groups = _all(attributes, GROUP_ATTRIBUTES)
    lowered = {g.lower() for g in groups}
    if settings.oidc_admin_group.lower() in lowered:
        role = "ADMIN"
    elif settings.oidc_analyst_group.lower() in lowered:
        role = "ANALYST"
    else:
        role = "VIEWER"

    return {
        "external_id": name_id or email,
        "email": email,
        "name": _first(attributes, ("displayname", "name", "cn")) or email,
        "role": role,
        "department": _first(attributes, DEPARTMENT_ATTRIBUTES) or "Engineering",
        "teams": groups,
    }


def parse_and_verify_response(saml_response_b64: str, request_id: str | None = None) -> dict:
    """Verify a base64 ``SAMLResponse`` and return the mapped user fields.

    Raises :class:`SAMLError` on anything that is not a cryptographically valid,
    in-window, correctly addressed, first-time-seen assertion from the
    configured IdP. There is no path through this function that trusts unsigned
    input.
    """
    config = get_saml_config()
    if not config.enabled:
        raise SAMLError("SAML is not configured")

    try:
        raw = base64.b64decode(saml_response_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise SAMLError(f"SAMLResponse is not valid base64: {e}")
    if not raw:
        raise SAMLError("SAMLResponse is empty")

    root = _parse_xml(raw)
    if root.tag != f"{{{NS['samlp']}}}Response":
        raise SAMLError(f"Expected a samlp:Response, got {root.tag}")

    if root.find(".//saml:EncryptedAssertion", NS) is not None:
        raise SAMLError(
            "Encrypted assertions are not supported — configure the IdP to sign "
            "the assertion without encrypting it, over TLS"
        )

    _check_status(root)

    # Exactly one assertion, anywhere in the document. A response carrying a
    # genuine signed assertion *and* a forged one is the classic wrapping shape:
    # even though only the signed subtree is read, there is no legitimate reason
    # for the extra element, so the whole response is refused.
    assertions = root.findall(".//saml:Assertion", NS)
    if len(assertions) != 1:
        raise SAMLError(f"Expected exactly one assertion, found {len(assertions)}")

    issuer = (root.findtext("saml:Issuer", default="", namespaces=NS) or "").strip()
    signed_xml = _verify_signature(root, config.idp_certificate)
    assertion = _assertion_from(signed_xml, root)

    # Prefer the issuer inside the signed assertion; the response-level one is
    # only advisory because it may sit outside the signature.
    assertion_issuer = (assertion.findtext("saml:Issuer", default="", namespaces=NS) or "").strip()
    effective_issuer = assertion_issuer or issuer
    if effective_issuer != config.idp_entity_id:
        raise SAMLError(
            f"Assertion issuer {effective_issuer!r} is not the configured IdP"
        )

    destination = (root.get("Destination") or "").strip()
    if destination and destination.rstrip("/") != config.sp_acs_url.rstrip("/"):
        raise SAMLError(f"Response destination {destination} is not this ACS endpoint")

    now = datetime.now(UTC)
    expires_at = _check_conditions(assertion, config, now)
    _check_subject(assertion, config, now, request_id)

    assertion_id = assertion.get("ID")
    if not assertion_id:
        raise SAMLError("Assertion has no ID")
    if not _replay_guard.claim(assertion_id, expires_at.timestamp()):
        raise SAMLError("Assertion has already been used")

    fields = map_assertion(assertion)
    logger.info("SAML assertion accepted for %s (role=%s)", fields["email"], fields["role"])
    return fields
