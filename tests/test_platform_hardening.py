"""Tests for the platform-hardening features: token revocation, readiness
probing, self-observability metrics, and list pagination."""

import pytest
from fastapi.testclient import TestClient

from denoiser.api.auth import get_password_hash
from denoiser.api.main import app
from denoiser.storage.db import SessionLocal, Tenant, User, init_db


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()


def _make_user(email: str, role: str = "ADMIN") -> None:
    db = SessionLocal()
    try:
        db.query(User).filter(User.email == email).delete()
        db.commit()
        tenant = db.query(Tenant).order_by(Tenant.id).first()
        db.add(User(
            email=email,
            hashed_password=get_password_hash("password123"),
            role=role,
            tenant_id=tenant.id if tenant else 1,
            is_active=True,
        ))
        db.commit()
    finally:
        db.close()


def _login(client: TestClient, email: str) -> str:
    return client.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


class TestTokenRevocation:
    def test_logout_revokes_token(self):
        _make_user("logout-user@semanticos.io")
        with TestClient(app) as client:
            token = _login(client, "logout-user@semanticos.io")
            headers = {"Authorization": f"Bearer {token}"}

            # Token works before logout.
            assert client.get("/auth/me", headers=headers).status_code == 200

            # Logout revokes it.
            assert client.post("/auth/logout", headers=headers).status_code == 200

            # Same token is now rejected.
            assert client.get("/auth/me", headers=headers).status_code == 401


class TestReadiness:
    def test_liveness_is_cheap_and_ok(self):
        with TestClient(app) as client:
            res = client.get("/health/live")
            assert res.status_code == 200
            assert res.json()["status"] == "healthy"

    def test_readiness_reports_component_checks(self):
        with TestClient(app) as client:
            res = client.get("/health/ready")
            body = res.json()
            # Status code is 200 or 503, but the component map is always present.
            assert set(body["checks"].keys()) == {
                "database", "redis", "clickhouse", "kafka", "ingestion_consumer",
            }
            assert body["checks"]["database"] == "ok"


class TestMetricsEndpoint:
    def test_prometheus_exposition(self):
        with TestClient(app) as client:
            client.get("/health/live")  # generate at least one observation
            res = client.get("/internal/metrics")
            assert res.status_code == 200
            assert "semanticos_http_requests_total" in res.text
            assert "semanticos_http_request_duration_seconds_bucket" in res.text


class TestPagination:
    def test_incidents_respects_limit(self):
        _make_user("page-admin@semanticos.io")
        with TestClient(app) as client:
            token = _login(client, "page-admin@semanticos.io")
            headers = {"Authorization": f"Bearer {token}"}
            res = client.get("/incidents?limit=1&offset=0", headers=headers)
            assert res.status_code == 200
            assert len(res.json()) <= 1

    def test_limit_is_bounded(self):
        _make_user("page-admin2@semanticos.io")
        with TestClient(app) as client:
            token = _login(client, "page-admin2@semanticos.io")
            headers = {"Authorization": f"Bearer {token}"}
            # Over the max — FastAPI validation rejects it.
            assert client.get("/incidents?limit=99999", headers=headers).status_code == 422
