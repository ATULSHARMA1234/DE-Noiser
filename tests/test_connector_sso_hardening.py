"""Fail-closed hardening for simulated connectors and the SAML stub.

- AWS/Docker connectors must return a real 502 in production instead of fake
  "simulated" data (matching the k8s connector).
- The SAML ACS endpoint must never mint a session from unverified input when the
  mock IdP is disabled.
"""

import pytest
from fastapi.testclient import TestClient

from denoiser.api.auth import create_access_token, get_password_hash


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
        import denoiser.api.main as main
        monkeypatch.setattr(main, "_simulated_connectors_allowed", lambda: False)
        # No AWS creds in the test env → the real path fails → 502, not fake data.
        res = client.get("/connectors/aws/groups", headers=analyst_auth)
        assert res.status_code == 502

    def test_docker_containers_502_when_simulation_disabled(self, client, analyst_auth, monkeypatch):
        import denoiser.api.main as main
        monkeypatch.setattr(main, "_simulated_connectors_allowed", lambda: False)
        res = client.get("/connectors/docker/containers", headers=analyst_auth)
        assert res.status_code == 502

    def test_aws_fetch_502_when_simulation_disabled(self, client, analyst_auth, monkeypatch):
        import denoiser.api.main as main
        monkeypatch.setattr(main, "_simulated_connectors_allowed", lambda: False)
        res = client.post("/connectors/aws/fetch", headers=analyst_auth,
                          data={"log_group": "/aws/lambda/x"})
        assert res.status_code == 502

    def test_simulated_still_available_in_sandbox(self, client, analyst_auth, monkeypatch):
        import denoiser.api.main as main
        monkeypatch.setattr(main, "_simulated_connectors_allowed", lambda: True)
        res = client.get("/connectors/aws/groups", headers=analyst_auth)
        assert res.status_code == 200
        assert res.json()["status"] == "simulated"


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
