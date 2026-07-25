"""Real OIDC Authorization Code flow.

Stands up a fake IdP (discovery + token + JWKS endpoints via respx) and signs a
genuine RS256 ID token, proving SemanticOS validates the signature/issuer/
audience and provisions the user with a role derived from group claims.
"""

import os
import time

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwk, jwt

ISSUER = "https://idp.example.com"
CLIENT_ID = "semanticos-client"
CLIENT_SECRET = "shhh-secret"
KID = "test-key-1"


@pytest.fixture(scope="module")
def rsa_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    public_jwk = jwk.construct(public_pem, "RS256").to_dict()
    public_jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return private_pem, {"keys": [public_jwk]}


@pytest.fixture(scope="module", autouse=True)
def _configure_oidc():
    os.environ.update({
        "OIDC_ISSUER": ISSUER,
        "OIDC_CLIENT_ID": CLIENT_ID,
        "OIDC_CLIENT_SECRET": CLIENT_SECRET,
        "OIDC_ADMIN_GROUP": "semanticos-admins",
    })
    from denoiser.settings import reload_settings
    reload_settings()
    from denoiser.storage.db import init_db
    init_db()
    yield
    for k in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_ADMIN_GROUP"):
        os.environ.pop(k, None)
    reload_settings()


@pytest.fixture(autouse=True)
def _clear_oidc_cache():
    from denoiser.api import oidc
    oidc._discovery_cache.clear()
    oidc._jwks_cache.clear()


def _discovery_doc():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
    }


def _sign_id_token(private_pem, groups):
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "idp-subject-42",
        "email": "sso.employee@bigcorp.com",
        "name": "SSO Employee",
        "groups": groups,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": KID})


def test_login_redirects_to_provider():
    from denoiser.api.main import app
    with respx.mock:
        respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=httpx.Response(200, json=_discovery_doc())
        )
        client = TestClient(app)
        res = client.get("/auth/sso/login", follow_redirects=False)
        assert res.status_code == 307
        assert res.headers["location"].startswith(f"{ISSUER}/authorize")
        assert f"client_id={CLIENT_ID}" in res.headers["location"]
        assert "state=" in res.headers["location"]


def test_callback_validates_and_provisions_admin(rsa_keys):
    private_pem, jwks = rsa_keys
    id_token = _sign_id_token(private_pem, groups=["semanticos-admins", "platform"])

    from denoiser.api.main import app
    with respx.mock:
        respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=httpx.Response(200, json=_discovery_doc())
        )
        respx.post(f"{ISSUER}/token").mock(
            return_value=httpx.Response(200, json={"id_token": id_token, "token_type": "bearer"})
        )
        respx.get(f"{ISSUER}/jwks").mock(return_value=httpx.Response(200, json=jwks))

        client = TestClient(app)
        res = client.get("/auth/sso/callback?code=real_auth_code", follow_redirects=False)
        assert res.status_code == 200, res.text
        body = res.json()
        assert "access_token" in body
        # Group "semanticos-admins" maps to the ADMIN role, teams mirror the claim.
        assert body["user"]["email"] == "sso.employee@bigcorp.com"
        assert body["user"]["role"] == "ADMIN"
        assert "platform" in body["user"]["teams"]


def test_callback_rejects_tampered_token(rsa_keys):
    private_pem, jwks = rsa_keys
    id_token = _sign_id_token(private_pem, groups=["viewers"])
    tampered = id_token[:-4] + ("aaaa" if not id_token.endswith("aaaa") else "bbbb")

    from denoiser.api.main import app
    with respx.mock:
        respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=httpx.Response(200, json=_discovery_doc())
        )
        respx.post(f"{ISSUER}/token").mock(
            return_value=httpx.Response(200, json={"id_token": tampered, "token_type": "bearer"})
        )
        respx.get(f"{ISSUER}/jwks").mock(return_value=httpx.Response(200, json=jwks))

        client = TestClient(app)
        res = client.get("/auth/sso/callback?code=real_auth_code", follow_redirects=False)
        assert res.status_code == 401
