"""
Integration tests for Task 10: Multi-source batch analysis.
"""

from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient
from denoiser.api.main import app


class TestMultiSourceAnalysis:
    """Integration tests for multi-source log analysis."""

    @pytest.fixture
    def client(self):
        from denoiser.api.auth import get_current_user
        from denoiser.storage.db import User
        
        mock_user = User(id=1, email="admin@semanticos.io", role="ADMIN")
        app.dependency_overrides[get_current_user] = lambda: mock_user
        
        with TestClient(app) as c:
            yield c
            
        app.dependency_overrides.clear()

    def test_multi_source_analysis_success(self, client, tmp_path):
        """Analyze should merge multiple sources and return combined results."""
        # 1. Create two log files representing two microservices
        log_a = tmp_path / "payment_service.log"
        log_a.write_text(
            "2026-05-22T23:00:00.000Z [INFO] payment-service started\n"
            "2026-05-22T23:00:01.000Z [ERROR] Failed to charge card for user_123: Timeout\n"
        )

        log_b = tmp_path / "order_service.log"
        log_b.write_text(
            "2026-05-22T23:00:01.100Z [WARN] order-service: Payment confirmation delayed\n"
            "2026-05-22T23:00:02.000Z [ERROR] Order processing failed for order_999\n"
        )

        payload = {
            "source": str(log_a),  # Fallback source (required by schema)
            "sources": [str(log_a), str(log_b)],
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

    def test_multi_source_one_invalid(self, client, tmp_path):
        """If one source is invalid, the other should still be successfully parsed."""
        log_valid = tmp_path / "valid.log"
        log_valid.write_text("2026-05-22T23:00:00.000Z [INFO] Valid log entry\n")
        
        payload = {
            "source": str(log_valid),
            "sources": [str(log_valid), "/path/to/nonexistent/file.log"],
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
