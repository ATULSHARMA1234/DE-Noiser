"""Reachability of the SSO flows, and the GitHub client's connection to the app.

Two features were fully implemented and unreachable:

- SAML verifies signatures, resists signature-wrapping and guards replay, but
  the login page offered a single hardcoded button wired to OIDC. A SAML-only
  deployment had a working endpoint no user could get to.
- GitHubIntegration implements Actions log fetching, issue creation and
  deployment sync, and was never instantiated anywhere in the codebase. The
  marketplace stored a row; nothing read it.
"""

import pytest
from fastapi.testclient import TestClient

from denoiser.api.auth import create_access_token, get_password_hash
from denoiser.storage.db import Integration as DBIntegration
from denoiser.storage.db import SessionLocal, Tenant, User, init_db


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client():
    from denoiser.api.main import app
    return TestClient(app)


@pytest.fixture
def admin_auth():
    db = SessionLocal()
    email = "wiring-admin@semanticos.io"
    try:
        db.query(User).filter(User.email == email).delete()
        db.commit()
        tenant = db.query(Tenant).order_by(Tenant.id).first()
        db.add(User(
            email=email, hashed_password=get_password_hash("password123"),
            role="ADMIN", tenant_id=tenant.id if tenant else 1, is_active=True,
        ))
        db.commit()
        yield {"Authorization": f"Bearer {create_access_token(data={'sub': email})}"}
    finally:
        db.query(User).filter(User.email == email).delete()
        db.commit()
        db.close()


class TestSsoProviderDiscovery:
    def test_providers_endpoint_is_reachable_without_auth(self, client):
        """The login page has to call it before anyone is signed in."""
        assert client.get("/auth/sso/providers").status_code == 200

    def test_every_flow_is_reported_with_a_start_url(self, client):
        body = client.get("/auth/sso/providers").json()
        for flow in ("oidc", "saml", "mock"):
            assert flow in body, f"{flow} missing from provider discovery"
            assert body[flow]["start_url"].startswith("/auth/sso/")
            assert isinstance(body[flow]["enabled"], bool)
            assert body[flow]["label"]

    def test_saml_start_url_matches_the_real_route(self, client):
        from denoiser.api.main import app

        saml = client.get("/auth/sso/providers").json()["saml"]
        assert saml["start_url"] == "/auth/sso/saml/login"
        assert saml["start_url"] in {getattr(r, "path", "") for r in app.routes}

    def test_saml_is_advertised_when_configured(self, client, monkeypatch):
        monkeypatch.setattr("denoiser.api.saml.saml_enabled", lambda: True)
        assert client.get("/auth/sso/providers").json()["saml"]["enabled"] is True

    def test_unconfigured_saml_is_not_advertised(self, client, monkeypatch):
        monkeypatch.setattr("denoiser.api.saml.saml_enabled", lambda: False)
        assert client.get("/auth/sso/providers").json()["saml"]["enabled"] is False

    def test_mock_is_hidden_once_real_oidc_is_configured(self, client, monkeypatch):
        """Otherwise a production login page offers a sandbox button."""
        from denoiser.settings import InfraSettings

        monkeypatch.setattr("denoiser.api.sso._mock_sso_enabled", lambda: True)
        monkeypatch.setattr(
            "denoiser.settings.get_settings",
            lambda: InfraSettings(
                oidc_issuer="https://idp.example.com",
                oidc_client_id="id",
                oidc_client_secret="secret",
            ),
        )
        body = client.get("/auth/sso/providers").json()
        assert body["oidc"]["enabled"] is True
        assert body["mock"]["enabled"] is False


class TestGitHubWiring:
    @pytest.fixture
    def github_integration(self, client, admin_auth):
        res = client.post("/integrations", headers=admin_auth, json={
            "provider": "github",
            "name": "GitHub",
            "config": {"api_key": "ghp_token", "repo": "acme/payments"},
        })
        assert res.status_code == 200, res.text
        created = res.json()
        yield created
        client.delete(f"/integrations/{created['id']}", headers=admin_auth)

    def test_stored_row_builds_a_configured_client(self, github_integration):
        from denoiser.api.integrations import _provider_for

        db = SessionLocal()
        try:
            row = db.query(DBIntegration).filter(DBIntegration.id == github_integration["id"]).first()
            client_obj = _provider_for(row)
        finally:
            db.close()

        assert client_obj is not None, "the marketplace row never reached GitHubIntegration"
        assert client_obj.repo == "acme/payments"
        assert client_obj.api_token == "ghp_token"

    def test_repo_survives_a_configure_round_trip(self, client, admin_auth, github_integration):
        """The dialog had no repo field, so a UI-configured GitHub could not work."""
        client.put(
            f"/integrations/{github_integration['id']}",
            headers=admin_auth,
            json={"config": {"repo": "acme/billing"}},
        )
        db = SessionLocal()
        try:
            row = db.query(DBIntegration).filter(DBIntegration.id == github_integration["id"]).first()
            assert row.config["repo"] == "acme/billing"
            assert row.config["api_key"] == "ghp_token", "the token must survive a repo-only edit"
        finally:
            db.close()

    def test_test_endpoint_reports_a_failure_instead_of_raising(self, client, admin_auth, github_integration):
        """An unreachable GitHub is a status, not a 500."""
        res = client.post(f"/integrations/{github_integration['id']}/test", headers=admin_auth)
        assert res.status_code == 200
        assert res.json()["status"] in ("ok", "failed")

    def test_sync_imports_deployments_as_markers(self, client, admin_auth, github_integration, monkeypatch):
        from denoiser.integrations.github import GitHubIntegration
        from denoiser.storage.db import DeploymentMarker

        monkeypatch.setattr(GitHubIntegration, "sync_metadata", lambda self: {
            "provider": "GitHub",
            "repo": "acme/payments",
            "default_branch": "main",
            "deployments": [
                {"sha": "abc123def4567890", "ref": "main", "environment": "production",
                 "created_at": "2026-07-20T10:00:00Z", "description": "Release 2.1"},
                {"sha": "0987654321fedcba", "ref": "main", "environment": "staging",
                 "created_at": "2026-07-21T10:00:00Z", "description": None},
            ],
            "latest_release": {"tag": "v2.1.0"},
            "synced_at": "2026-07-26T00:00:00Z",
        })

        res = client.post(f"/integrations/{github_integration['id']}/sync", headers=admin_auth)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["deployments_imported"] == 2
        assert body["latest_release"] == "v2.1.0"

        db = SessionLocal()
        try:
            markers = db.query(DeploymentMarker).filter(
                DeploymentMarker.service == "payments",
                DeploymentMarker.version.in_(["abc123def456", "0987654321fe"]),
            ).all()
            assert len(markers) == 2
            assert {m.environment for m in markers} == {"production", "staging"}

            # A second sync must not duplicate what is already recorded.
            again = client.post(f"/integrations/{github_integration['id']}/sync", headers=admin_auth)
            assert again.json()["deployments_imported"] == 0

            for marker in markers:
                db.delete(marker)
            db.commit()
        finally:
            db.close()

    def test_sync_refuses_a_provider_without_an_implementation(self, client, admin_auth):
        created = client.post("/integrations", headers=admin_auth, json={
            "provider": "pagerduty", "name": "PD", "config": {"api_key": "x"},
        }).json()
        try:
            res = client.post(f"/integrations/{created['id']}/sync", headers=admin_auth)
            assert res.status_code == 400
            assert "not implemented" in res.json()["detail"]
        finally:
            client.delete(f"/integrations/{created['id']}", headers=admin_auth)
