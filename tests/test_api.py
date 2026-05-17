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
    """Create a test client for the SemanticOS API."""
    from denoiser.api.main import app
    with TestClient(app) as c:
        yield c


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
