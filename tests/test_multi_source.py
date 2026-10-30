"""
Integration tests for Task 10: Multi-source batch analysis.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from denoiser.api.main import app
from denoiser.api.sources import tenant_dir

TENANT_ID = 1


class TestMultiSourceAnalysis:
    """Integration tests for multi-source log analysis."""

    @pytest.fixture
    def client(self):
        from denoiser.api.auth import get_current_user
        from denoiser.storage.db import User

        mock_user = User(id=1, email="admin@semanticos.io", role="ADMIN", tenant_id=TENANT_ID)
        app.dependency_overrides[get_current_user] = lambda: mock_user

        with TestClient(app) as c:
            yield c

        app.dependency_overrides.clear()

    @pytest.fixture
    def source_dir(self):
        """This tenant's source directory, emptied of anything a test wrote.

        Sources have to live here rather than in a pytest tmp_path: /analyze
        resolves every path against the caller's own directory, so that an
        analyst cannot ask the platform to read /etc/passwd or its own .env.
        """
        directory = tenant_dir(TENANT_ID)
        written: list = []
        yield directory, written
        for path in written:
            path.unlink(missing_ok=True)

    def test_multi_source_analysis_success(self, client, source_dir):
        """Analyze should merge multiple sources and return combined results."""
        directory, written = source_dir

        # 1. Create two log files representing two microservices
        log_a = directory / "payment_service.log"
        log_a.write_text(
            "2026-05-22T23:00:00.000Z [INFO] payment-service started\n"
            "2026-05-22T23:00:01.000Z [ERROR] Failed to charge card for user_123: Timeout\n"
        )
        written.append(log_a)

        log_b = directory / "order_service.log"
        log_b.write_text(
            "2026-05-22T23:00:01.100Z [WARN] order-service: Payment confirmation delayed\n"
            "2026-05-22T23:00:02.000Z [ERROR] Order processing failed for order_999\n"
        )
        written.append(log_b)

        payload = {
            "source": log_a.name,  # Fallback source (required by schema)
            "sources": [log_a.name, log_b.name],
            "intelligence": False,  # Disable LLM to keep test fast/local
            "top_n": 5
        }

        # 2. Trigger multi-source analysis
        response = client.post("/analyze", json=payload)

        # 3. Assertions
        assert response.status_code == 200
        data = response.json()

        assert "total_logs" in data
        assert data["total_logs"] == 4

        assert "clusters" in data
        assert isinstance(data["clusters"], list)
        assert len(data["clusters"]) > 0

        # Verify that all logs are processed and accounted for in the clusters
        clusters = data["clusters"]
        assert sum(c["size"] for c in clusters) == 4

    def test_multi_source_one_invalid(self, client, source_dir):
        """If one source is invalid, the other should still be successfully parsed."""
        directory, written = source_dir

        log_valid = directory / "valid.log"
        log_valid.write_text("2026-05-22T23:00:00.000Z [INFO] Valid log entry\n")
        written.append(log_valid)

        payload = {
            "source": log_valid.name,
            "sources": [log_valid.name, "nonexistent-file.log"],
            "intelligence": False
        }

        response = client.post("/analyze", json=payload)

        # Valid source should still be analyzed successfully
        assert response.status_code == 200
        data = response.json()
        assert data["total_logs"] == 1
        assert "Valid log entry" in data["clusters"][0]["representative_log"]

    def test_multi_source_all_invalid(self, client):
        """If all sources are invalid, should return 404."""
        payload = {
            "source": "/invalid/path/1.log",
            "sources": ["/invalid/path/1.log", "/invalid/path/2.log"],
            "intelligence": False
        }

        response = client.post("/analyze", json=payload)
        assert response.status_code == 404

    def test_source_outside_the_data_root_is_rejected(self, client, tmp_path):
        """An absolute path to a file the tenant does not own must not be read.

        This is the arbitrary-file-read case: without confinement, `source`
        went straight to the log reader, so any analyst account could read the
        deployment's own secrets back out of the run results.
        """
        outside = tmp_path / "secrets.env"
        outside.write_text("SLD_LLM_API_KEY=super-secret-value\n")

        response = client.post(
            "/analyze",
            json={"source": str(outside), "intelligence": False},
        )
        assert response.status_code == 404
        assert "super-secret-value" not in response.text

    def test_another_tenants_upload_is_not_readable(self, client):
        """Files belonging to a different tenant are invisible, not just unlisted."""
        other = tenant_dir(9999) / "their-private.log"
        other.write_text("2026-05-22T23:00:00.000Z [ERROR] other tenant private data\n")
        try:
            response = client.post(
                "/analyze",
                json={"source": "their-private.log", "intelligence": False},
            )
            assert response.status_code == 404
            assert "other tenant private data" not in response.text
        finally:
            other.unlink(missing_ok=True)
