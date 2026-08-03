"""Tests for the SAML 2.0 service provider.

The ACS endpoint previously minted a session from unverified input. These tests
sign real assertions with a throwaway key and then check both directions: a
genuine assertion authenticates, and every forgery route — wrong key, unsigned,
signature wrapping, wrong audience, expired, replayed — is refused.
"""

import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from lxml import etree
from signxml import XMLSigner

from denoiser.api.main import app
from denoiser.api.saml import (
    NS,
    SAMLError,
    build_authn_request,
    build_sp_metadata,
    parse_and_verify_response,
    reset_replay_guard,
    saml_enabled,
)
from denoiser.storage.db import User, init_db

IDP_ENTITY_ID = "https://idp.example.com/metadata"
IDP_SSO_URL = "https://idp.example.com/sso/redirect"
SP_ENTITY_ID = "https://semanticos.example.com/sp"
SP_ACS_URL = "https://semanticos.example.com/auth/sso/saml/acs"


def _make_keypair():
    """A throwaway signing key and its self-signed certificate."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-idp")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return key_pem, cert_pem


IDP_KEY, IDP_CERT = _make_keypair()
OTHER_KEY, OTHER_CERT = _make_keypair()


def _instant(offset_minutes: float = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=offset_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _response_xml(
    *,
    assertion_id: str = "_assertion-1",
    email: str = "saml-user@semanticos.io",
    groups: tuple[str, ...] = ("semanticos-admins", "platform"),
    audience: str = SP_ENTITY_ID,
    issuer: str = IDP_ENTITY_ID,
    destination: str = SP_ACS_URL,
    recipient: str = SP_ACS_URL,
    not_before_minutes: float = -5,
    not_after_minutes: float = 5,
    status: str = "urn:oasis:names:tc:SAML:2.0:status:Success",
) -> str:
    group_values = "".join(
        f'<saml:AttributeValue>{g}</saml:AttributeValue>' for g in groups
    )
    return f"""<samlp:Response xmlns:samlp="{NS['samlp']}" xmlns:saml="{NS['saml']}"
        ID="_response-1" Version="2.0" IssueInstant="{_instant()}" Destination="{destination}">
      <saml:Issuer>{issuer}</saml:Issuer>
      <samlp:Status><samlp:StatusCode Value="{status}"/></samlp:Status>
      <saml:Assertion ID="{assertion_id}" Version="2.0" IssueInstant="{_instant()}">
        <saml:Issuer>{issuer}</saml:Issuer>
        <saml:Subject>
          <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{email}</saml:NameID>
          <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
            <saml:SubjectConfirmationData NotOnOrAfter="{_instant(not_after_minutes)}" Recipient="{recipient}"/>
          </saml:SubjectConfirmation>
        </saml:Subject>
        <saml:Conditions NotBefore="{_instant(not_before_minutes)}" NotOnOrAfter="{_instant(not_after_minutes)}">
          <saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>
        </saml:Conditions>
        <saml:AttributeStatement>
          <saml:Attribute Name="email"><saml:AttributeValue>{email}</saml:AttributeValue></saml:Attribute>
          <saml:Attribute Name="groups">{group_values}</saml:Attribute>
          <saml:Attribute Name="department"><saml:AttributeValue>Platform</saml:AttributeValue></saml:Attribute>
        </saml:AttributeStatement>
      </saml:Assertion>
    </samlp:Response>"""


def _sign(xml: str, *, key: str = IDP_KEY, cert: str = IDP_CERT, sign_assertion: bool = True) -> str:
    """Sign the assertion (or the whole response) and return the document."""
    root = etree.fromstring(xml.encode())
    target = root.find("saml:Assertion", NS) if sign_assertion else root
    signer = XMLSigner(
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    )
    signed = signer.sign(target, key=key, cert=cert, reference_uri=target.get("ID"))
    if sign_assertion:
        root.replace(target, signed)
        return etree.tostring(root).decode()
    return etree.tostring(signed).decode()


def _encode(xml: str) -> str:
    return base64.b64encode(xml.encode()).decode()


def _signed_response(**kwargs) -> str:
    sign_assertion = kwargs.pop("sign_assertion", True)
    key = kwargs.pop("key", IDP_KEY)
    cert = kwargs.pop("cert", IDP_CERT)
    return _encode(_sign(_response_xml(**kwargs), key=key, cert=cert, sign_assertion=sign_assertion))


@pytest.fixture(autouse=True)
def saml_config(monkeypatch):
    monkeypatch.setenv("SAML_IDP_ENTITY_ID", IDP_ENTITY_ID)
    monkeypatch.setenv("SAML_IDP_SSO_URL", IDP_SSO_URL)
    monkeypatch.setenv("SAML_IDP_X509_CERT", IDP_CERT)
    monkeypatch.setenv("SAML_SP_ENTITY_ID", SP_ENTITY_ID)
    monkeypatch.setenv("SAML_SP_ACS_URL", SP_ACS_URL)
    reset_replay_guard()
    yield
    reset_replay_guard()


class TestConfiguration:
    def test_saml_is_enabled_when_fully_configured(self):
        assert saml_enabled() is True

    def test_saml_is_disabled_when_a_setting_is_missing(self, monkeypatch):
        monkeypatch.delenv("SAML_IDP_X509_CERT")
        assert saml_enabled() is False

    def test_authn_request_is_a_deflated_redirect(self):
        url, request_id = build_authn_request(relay_state="/dashboard")
        assert url.startswith(IDP_SSO_URL)
        assert "SAMLRequest=" in url and "RelayState=%2Fdashboard" in url
        assert request_id.startswith("_")

    def test_sp_metadata_advertises_the_acs_endpoint(self):
        metadata = build_sp_metadata()
        assert SP_ENTITY_ID in metadata
        assert SP_ACS_URL in metadata
        assert 'WantAssertionsSigned="true"' in metadata


class TestValidAssertions:
    def test_signed_assertion_maps_to_user_fields(self):
        fields = parse_and_verify_response(_signed_response())
        assert fields["email"] == "saml-user@semanticos.io"
        assert fields["role"] == "ADMIN"  # semanticos-admins group
        assert fields["department"] == "Platform"
        assert "platform" in fields["teams"]

    def test_response_level_signature_is_accepted(self):
        fields = parse_and_verify_response(_signed_response(sign_assertion=False))
        assert fields["email"] == "saml-user@semanticos.io"

    def test_group_membership_decides_the_role(self):
        viewer = parse_and_verify_response(
            _signed_response(groups=("some-unrelated-group",), assertion_id="_a-viewer")
        )
        assert viewer["role"] == "VIEWER"
        analyst = parse_and_verify_response(
            _signed_response(groups=("semanticos-analysts",), assertion_id="_a-analyst")
        )
        assert analyst["role"] == "ANALYST"


class TestForgeryIsRejected:
    def test_unsigned_assertion_is_rejected(self):
        with pytest.raises(SAMLError, match="no signature"):
            parse_and_verify_response(_encode(_response_xml()))

    def test_signature_by_another_key_is_rejected(self):
        forged = _signed_response(key=OTHER_KEY, cert=OTHER_CERT)
        with pytest.raises(SAMLError, match="verification failed"):
            parse_and_verify_response(forged)

    def test_tampered_attribute_after_signing_is_rejected(self):
        signed = _sign(_response_xml())
        tampered = signed.replace("saml-user@semanticos.io", "attacker@evil.io")
        with pytest.raises(SAMLError, match="verification failed"):
            parse_and_verify_response(_encode(tampered))

    def test_signature_wrapping_is_rejected(self):
        """A signed assertion with a forged sibling must not authenticate anyone."""
        signed = _sign(_response_xml())
        root = etree.fromstring(signed.encode())
        genuine = root.find("saml:Assertion", NS)
        forged = etree.fromstring(
            _response_xml(assertion_id="_forged", email="attacker@evil.io").encode()
        ).find("saml:Assertion", NS)
        root.insert(list(root).index(genuine), forged)
        with pytest.raises(SAMLError):
            parse_and_verify_response(_encode(etree.tostring(root).decode()))

    def test_assertion_for_another_audience_is_rejected(self):
        with pytest.raises(SAMLError, match="addressed to"):
            parse_and_verify_response(_signed_response(audience="https://other-service.example.com"))

    def test_assertion_from_another_issuer_is_rejected(self):
        with pytest.raises(SAMLError, match="not the configured IdP"):
            parse_and_verify_response(_signed_response(issuer="https://evil-idp.example.com"))

    def test_expired_assertion_is_rejected(self):
        with pytest.raises(SAMLError, match="expired"):
            parse_and_verify_response(
                _signed_response(not_before_minutes=-30, not_after_minutes=-10)
            )

    def test_not_yet_valid_assertion_is_rejected(self):
        with pytest.raises(SAMLError, match="not yet valid"):
            parse_and_verify_response(
                _signed_response(not_before_minutes=30, not_after_minutes=60)
            )

    def test_wrong_recipient_is_rejected(self):
        with pytest.raises(SAMLError, match="recipient"):
            parse_and_verify_response(_signed_response(recipient="https://evil.example.com/acs"))

    def test_wrong_destination_is_rejected(self):
        with pytest.raises(SAMLError, match="destination"):
            parse_and_verify_response(_signed_response(destination="https://evil.example.com/acs"))

    def test_failed_idp_status_is_rejected(self):
        with pytest.raises(SAMLError, match="rejected the authentication"):
            parse_and_verify_response(
                _signed_response(status="urn:oasis:names:tc:SAML:2.0:status:AuthnFailed")
            )

    def test_replayed_assertion_is_rejected(self):
        response = _signed_response()
        assert parse_and_verify_response(response)["email"] == "saml-user@semanticos.io"
        with pytest.raises(SAMLError, match="already been used"):
            parse_and_verify_response(response)

    def test_encrypted_assertion_is_refused_not_ignored(self):
        xml = _response_xml().replace(
            "<saml:Assertion", "<saml:EncryptedAssertion/><saml:Assertion", 1
        )
        with pytest.raises(SAMLError, match="Encrypted assertions are not supported"):
            parse_and_verify_response(_encode(xml))

    def test_doctype_is_refused(self):
        """XXE vector: a DOCTYPE has no legitimate place in a SAML response."""
        xml = '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>' + _response_xml()
        with pytest.raises(SAMLError):
            parse_and_verify_response(_encode(xml))

    def test_garbage_input_is_rejected(self):
        with pytest.raises(SAMLError, match="base64"):
            parse_and_verify_response("this is not base64!!")
        with pytest.raises(SAMLError, match="well-formed"):
            parse_and_verify_response(_encode("<not-xml"))


class TestACSEndpoint:
    @pytest.fixture(scope="class", autouse=True)
    def setup_db(self):
        init_db()

    def test_valid_post_issues_a_platform_token(self):
        from denoiser.storage.db import SessionLocal

        with TestClient(app) as client:
            resp = client.post("/auth/sso/saml/acs", data={"SAMLResponse": _signed_response()})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["access_token"] and body["token_type"] == "bearer"
            assert body["user"]["email"] == "saml-user@semanticos.io"
            assert body["user"]["role"] == "ADMIN"

        db = SessionLocal()
        try:
            db.query(User).filter(User.email == "saml-user@semanticos.io").delete()
            db.commit()
        finally:
            db.close()

    def test_forged_post_gets_401_and_no_token(self):
        with TestClient(app) as client:
            resp = client.post(
                "/auth/sso/saml/acs",
                data={"SAMLResponse": _signed_response(key=OTHER_KEY, cert=OTHER_CERT)},
            )
            assert resp.status_code == 401
            assert "access_token" not in resp.json()

    def test_missing_assertion_is_a_400(self):
        with TestClient(app) as client:
            assert client.post("/auth/sso/saml/acs", data={}).status_code == 400

    def test_metadata_endpoint_serves_sp_metadata(self):
        with TestClient(app) as client:
            resp = client.get("/auth/sso/saml/metadata")
            assert resp.status_code == 200
            assert SP_ACS_URL in resp.text

    def test_login_redirects_to_the_idp(self):
        with TestClient(app) as client:
            resp = client.get("/auth/sso/saml/login", follow_redirects=False)
            assert resp.status_code in (302, 307)
            assert resp.headers["location"].startswith(IDP_SSO_URL)

    def test_unconfigured_saml_login_is_501(self, monkeypatch):
        monkeypatch.delenv("SAML_IDP_X509_CERT")
        with TestClient(app) as client:
            resp = client.get("/auth/sso/saml/login", follow_redirects=False)
            assert resp.status_code == 501
