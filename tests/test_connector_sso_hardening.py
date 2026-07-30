"""Fail-closed hardening for simulated connectors and the SAML stub.

- AWS/Docker connectors must return a real 502 in production instead of fake
  "simulated" data (matching the k8s connector).
- The SAML ACS endpoint must never mint a session from unverified input when the
  mock IdP is disabled.
"""

import pytest
from fastapi.testclient import TestClient

from denoiser.api.auth import create_access_token, get_password_hash
from denoiser.integrations import connectors


@pytest.fixture(scope="module", autouse=True)
def _db():
    from denoiser.storage.db import init_db
    init_db()


@pytest.fixture(autouse=True)
def _unreachable_backends(monkeypatch):
    """Simulate unreachable AWS/Docker fast, so tests don't block on boto3's
    EC2-metadata credential retries or a real docker socket."""
    def _raise(*a, **k):
        raise ConnectionError("backend unreachable (test)")

    import boto3
    monkeypatch.setattr(boto3, "client", _raise)
    try:
        import docker
        monkeypatch.setattr(docker, "from_env", _raise)
    except ImportError:
        pass  # docker not installed → `import docker` already raises → 502


@pytest.fixture
def client():
    from denoiser.api.main import app
    return TestClient(app)


@pytest.fixture
def analyst_auth():
    from denoiser.storage.db import SessionLocal, User
    email = "connector-user@bigcorp.com"
    db = SessionLocal()
    db.query(User).filter(User.email == email).delete()
    db.add(User(email=email, hashed_password=get_password_hash("pw-123456"),
                role="ADMIN", tenant_id=1, is_active=True))
    db.commit()
    db.close()
    return {"Authorization": f"Bearer {create_access_token(data={'sub': email})}"}


class TestConnectorFailClosed:
    def test_aws_groups_502_when_simulation_disabled(self, client, analyst_auth, monkeypatch):
        monkeypatch.setattr(connectors, "simulated_allowed", lambda: False)
        # No AWS creds in the test env → the real path fails → 502, not fake data.
        res = client.get("/connectors/aws/groups", headers=analyst_auth)
        assert res.status_code == 502

    def test_docker_containers_502_when_simulation_disabled(self, client, analyst_auth, monkeypatch):
        monkeypatch.setattr(connectors, "simulated_allowed", lambda: False)
        res = client.get("/connectors/docker/containers", headers=analyst_auth)
        assert res.status_code == 502

    def test_aws_fetch_502_when_simulation_disabled(self, client, analyst_auth, monkeypatch):
        monkeypatch.setattr(connectors, "simulated_allowed", lambda: False)
        res = client.post("/connectors/aws/fetch", headers=analyst_auth,
                          data={"log_group": "/aws/lambda/x"})
        assert res.status_code == 502

    def test_simulated_still_available_in_sandbox(self, client, analyst_auth, monkeypatch):
        monkeypatch.setattr(connectors, "simulated_allowed", lambda: True)
        res = client.get("/connectors/aws/groups", headers=analyst_auth)
        assert res.status_code == 200
        assert res.json()["status"] == "simulated"


class TestSimulationGate:
    """The gate itself, which the tests above stub out.

    Production must fail closed; a developer checkout must not answer every
    connector page with a 502; and an explicit setting must win either way.
    """

    @staticmethod
    def _allowed(monkeypatch, *, environment: str, explicit: str | None) -> bool:
        from denoiser.settings import InfraSettings

        # `connectors.simulated_allowed` reads both of these from
        # denoiser.settings at call time, so patching them there is what counts.
        monkeypatch.setattr("denoiser.settings.is_testing", lambda: False)
        monkeypatch.setattr(
            "denoiser.settings.get_settings", lambda: InfraSettings(environment=environment)
        )
        if explicit is None:
            monkeypatch.delenv("ALLOW_SIMULATED_CONNECTORS", raising=False)
        else:
            monkeypatch.setenv("ALLOW_SIMULATED_CONNECTORS", explicit)
        return connectors.simulated_allowed()

    def test_production_fails_closed_by_default(self, monkeypatch):
        assert self._allowed(monkeypatch, environment="production", explicit=None) is False

    def test_development_simulates_by_default(self, monkeypatch):
        assert self._allowed(monkeypatch, environment="development", explicit=None) is True

    def test_explicit_opt_in_wins_in_production(self, monkeypatch):
        assert self._allowed(monkeypatch, environment="production", explicit="true") is True

    def test_explicit_opt_out_wins_in_development(self, monkeypatch):
        assert self._allowed(monkeypatch, environment="development", explicit="false") is False


class TestSamlFailClosed:
    def test_saml_acs_501_when_mock_disabled(self, client, monkeypatch):
        import denoiser.api.sso as sso
        monkeypatch.setattr(sso, "_mock_sso_enabled", lambda: False)
        res = client.post("/auth/sso/saml/acs")
        assert res.status_code == 501

    def test_saml_acs_issues_mock_session_in_sandbox(self, client, monkeypatch):
        import denoiser.api.sso as sso
        monkeypatch.setattr(sso, "_mock_sso_enabled", lambda: True)
        res = client.post("/auth/sso/saml/acs")
        assert res.status_code == 200
        assert "access_token" in res.json()

