"""
Task 8: Integration tests for all API endpoints.
Uses FastAPI's TestClient to test the full request/response lifecycle.
"""

import pytest
from fastapi.testclient import TestClient

# We need to handle the import carefully since the API imports heavy ML modules.
# For testing, we test the endpoints that don't require the ML pipeline.


@pytest.fixture(scope="module")
def client():
    """Create a test client for the SemanticOS API with bypassed authentication."""
    from denoiser.api.auth import get_current_user, verify_ingest_auth
    from denoiser.api.main import app
    from denoiser.storage.db import User

    # Carries a tenant: every source/webhook/audit path is tenant-scoped now,
    # so a user without one exercises the "unassigned" fallback rather than
    # the behaviour a real signed-in operator gets.
    mock_user = User(id=1, email="admin@semanticos.io", role="ADMIN", tenant_id=1)
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[verify_ingest_auth] = lambda: None

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


class TestHealthEndpoint:
    """Task 8: Test the /health endpoint."""

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_version(self, client):
        data = client.get("/health").json()
        assert "version" in data
        assert data["status"] == "healthy"


class TestSourcesEndpoint:
    """Task 8: Test the /sources endpoint."""

    def test_list_sources_returns_list(self, client):
        response = client.get("/sources")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestSettingsEndpoint:
    """Task 8: Test the /settings endpoints."""

    def test_get_settings_returns_dict(self, client):
        response = client.get("/settings")
        assert response.status_code == 200
        data = response.json()
        assert "redact_pii" in data
        assert "retention_days" in data

    def test_update_settings(self, client):
        response = client.put("/settings", json={"retention_days": 60})
        assert response.status_code == 200
        data = response.json()
        assert data["retention_days"] == 60

    def test_secrets_are_never_returned(self, client):
        from denoiser.api.platform_settings import load_settings
        from denoiser.api.routers_settings import SECRET_MASK

        client.put("/settings", json={"s3_access_key": "AKIAREAL", "s3_secret_key": "real-secret"})
        data = client.get("/settings").json()
        assert data["s3_access_key"] == SECRET_MASK
        assert data["s3_secret_key"] == SECRET_MASK

        # Saving the page round-trips the mask; that must not overwrite the key.
        client.put("/settings", json={**data, "retention_days": 61})
        stored = load_settings()
        assert stored["s3_access_key"] == "AKIAREAL"
        assert stored["s3_secret_key"] == "real-secret"


class TestIncidentsEndpoint:
    """Task 8: Test the /incidents endpoints."""

    def test_list_incidents_returns_list(self, client):
        response = client.get("/incidents")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_nonexistent_incident(self, client):
        response = client.get("/incidents/99999")
        assert response.status_code == 404


class TestRunsEndpoint:
    """Task 8: Test the /runs endpoints."""

    def test_list_runs_returns_list(self, client):
        response = client.get("/runs")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_nonexistent_run(self, client):
        response = client.get("/runs/nonexistent_id")
        assert response.status_code == 404


class TestIngestEndpoint:
    """Task 8: Test the /ingest endpoint."""

    def test_ingest_single_log(self, client):
        response = client.post("/ingest", json=[{"message": "test log line", "level": "INFO"}])
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["ingested"] == 1

    def test_ingest_batch(self, client):
        logs = [{"message": f"log line {i}"} for i in range(5)]
        response = client.post("/ingest", json=logs)
        assert response.status_code == 200
        assert response.json()["ingested"] == 5


class TestConnectorEndpoints:
    """Task 8: Test connector discovery endpoints (sandbox mode)."""

    def test_k8s_pods_returns_data(self, client):
        response = client.get("/connectors/k8s/pods")
        assert response.status_code == 200
        data = response.json()
        assert "pods" in data
        assert len(data["pods"]) > 0

    def test_aws_groups_returns_data(self, client):
        response = client.get("/connectors/aws/groups")
        assert response.status_code == 200
        data = response.json()
        assert "groups" in data
        assert len(data["groups"]) > 0

    def test_docker_containers_returns_data(self, client):
        response = client.get("/connectors/docker/containers")
        assert response.status_code == 200
        data = response.json()
        assert "containers" in data
        assert len(data["containers"]) > 0


class TestCorrelationIDMiddleware:
    """Task 8: Verify the correlation ID middleware is active."""

    def test_response_has_request_id_header(self, client):
        response = client.get("/health")
        assert "x-request-id" in response.headers

    def test_custom_request_id_is_echoed(self, client):
        response = client.get("/health", headers={"X-Request-ID": "test-123"})
        assert response.headers["x-request-id"] == "test-123"


class TestUploadPathTraversal:
    """Uploads must not be able to escape the data/ directory."""

    def test_traversal_filename_is_contained(self, client):
        resp = client.post(
            "/sources/upload",
            files={"file": ("../../escape.log", b"pwned", "text/plain")},
        )
        # The traversal is collapsed to a bare filename, and the destination
        # directory is chosen by the server from the authenticated tenant — the
        # client never influences where the file lands.
        assert resp.status_code == 200
        assert resp.json()["name"] == "escape.log"
        path = resp.json()["path"]
        assert ".." not in path
        # The response no longer discloses the server's absolute layout.
        assert path == "escape.log"

        from denoiser.api.sources import tenant_dir

        written = tenant_dir(1) / "escape.log"
        assert written.is_file(), "upload should land in the tenant's own directory"
        written.unlink()

    def test_dotdot_only_filename_rejected(self, client):
        resp = client.post(
            "/sources/upload",
            files={"file": ("..", b"pwned", "text/plain")},
        )
        assert resp.status_code == 400


class TestCors:
    def test_preflight_allows_the_csrf_header_the_web_client_sends(self, client, monkeypatch):
        """Every unsafe request from the web app carries X-CSRF-Token."""
        from denoiser.api.cookies import CSRF_HEADER
        from denoiser.settings import get_settings

        origin = get_settings().cors_origin_list[0]
        res = client.options("/settings", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": f"content-type,{CSRF_HEADER.lower()}",
        })
        assert res.status_code == 200
        assert CSRF_HEADER.lower() in res.headers["access-control-allow-headers"].lower()
