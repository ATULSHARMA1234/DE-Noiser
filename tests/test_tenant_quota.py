"""Tests for the per-tenant API quota.

The per-IP limiter only guards /ingest and buckets by client address, so a
tenant shipping from many pods was effectively unbounded. These cover the
tenant-keyed ceiling: tier lookup, exemptions, header reporting, and the
in-memory fallback when Redis is unreachable.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from denoiser.api.auth import create_access_token, get_password_hash
from denoiser.api.middleware import (
    DEFAULT_TENANT_QUOTAS,
    TenantQuotaMiddleware,
    _lookup_tenant,
)
from denoiser.storage.db import SessionLocal, Tenant, User, init_db


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()


@pytest.fixture
def tenant_and_user():
    """A pro-tier tenant with one user, cleaned up afterwards."""
    db = SessionLocal()
    try:
        db.query(User).filter(User.email == "quota-user@semanticos.io").delete()
        db.query(Tenant).filter(Tenant.name == "Quota Test Workspace").delete()
        db.commit()
        tenant = Tenant(name="Quota Test Workspace", api_key="quota-test-key", tier="pro")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        db.add(User(
            email="quota-user@semanticos.io",
            hashed_password=get_password_hash("password123"),
            role="ADMIN",
            tenant_id=tenant.id,
            is_active=True,
        ))
        db.commit()
        yield tenant.id
    finally:
        db.query(User).filter(User.email == "quota-user@semanticos.io").delete()
        db.query(Tenant).filter(Tenant.name == "Quota Test Workspace").delete()
        db.commit()
        db.close()


def _app_with_quota(**kwargs) -> FastAPI:
    """A minimal app wrapped in the quota middleware with Redis forced down,
    so the in-memory fallback path is what is exercised."""
    app = FastAPI()

    @app.get("/echo")
    def echo():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.add_middleware(TenantQuotaMiddleware, enabled=True, **kwargs)
    return app


def _break_redis(client: TestClient) -> None:
    """Point the middleware's Redis at a client whose pipeline always raises."""
    broken = MagicMock()
    pipe = MagicMock()
    pipe.execute = AsyncMock(side_effect=Exception("Redis down"))
    broken.pipeline.return_value.__aenter__.return_value = pipe
    mw = client.app.middleware_stack
    while mw is not None:
        if isinstance(mw, TenantQuotaMiddleware):
            mw.redis = broken
            mw._window.redis = broken
            return
        mw = getattr(mw, "app", None)
    raise AssertionError("TenantQuotaMiddleware not found in the stack")


class TestTenantResolution:
    def test_api_key_resolves_tenant_and_tier(self, tenant_and_user):
        assert _lookup_tenant("quota-test-key", None) == (str(tenant_and_user), "pro")

    def test_jwt_subject_resolves_tenant(self, tenant_and_user):
        assert _lookup_tenant(None, "quota-user@semanticos.io") == (str(tenant_and_user), "pro")

    def test_unknown_credential_resolves_to_nothing(self):
        assert _lookup_tenant("no-such-key", None) is None
        assert _lookup_tenant(None, "nobody@example.com") is None


class TestQuotaEnforcement:
    def test_requests_under_quota_pass_with_headers(self, tenant_and_user):
        app = _app_with_quota(quotas={"pro": 5})
        with TestClient(app) as client:
            _break_redis(client)
            resp = client.get("/echo", headers={"X-API-Key": "quota-test-key"})
            assert resp.status_code == 200
            assert resp.headers["X-RateLimit-Limit"] == "5"
            assert resp.headers["X-RateLimit-Remaining"] == "4"

    def test_tenant_over_quota_gets_429(self, tenant_and_user):
        app = _app_with_quota(quotas={"pro": 3})
        with TestClient(app) as client:
            _break_redis(client)
            headers = {"X-API-Key": "quota-test-key"}
            for _ in range(3):
                assert client.get("/echo", headers=headers).status_code == 200
            blocked = client.get("/echo", headers=headers)
            assert blocked.status_code == 429
            assert "Tenant quota" in blocked.json()["detail"]
            assert blocked.headers["Retry-After"] == "60"

    def test_quota_follows_the_jwt_bearer_too(self, tenant_and_user):
        """A user's token buckets to the same tenant as that tenant's API key."""
        app = _app_with_quota(quotas={"pro": 2})
        token = create_access_token(data={"sub": "quota-user@semanticos.io"})
        with TestClient(app) as client:
            _break_redis(client)
            assert client.get("/echo", headers={"X-API-Key": "quota-test-key"}).status_code == 200
            assert client.get("/echo", headers={"Authorization": f"Bearer {token}"}).status_code == 200
            over = client.get("/echo", headers={"Authorization": f"Bearer {token}"})
            assert over.status_code == 429

    def test_unauthenticated_requests_are_not_quota_bucketed(self):
        app = _app_with_quota(quotas={"free": 1})
        with TestClient(app) as client:
            _break_redis(client)
            for _ in range(5):
                assert client.get("/echo").status_code == 200

    def test_health_stays_reachable_over_quota(self, tenant_and_user):
        app = _app_with_quota(quotas={"pro": 1})
        with TestClient(app) as client:
            _break_redis(client)
            headers = {"X-API-Key": "quota-test-key"}
            assert client.get("/echo", headers=headers).status_code == 200
            assert client.get("/echo", headers=headers).status_code == 429
            # Probes and auth routes must not be locked out by a quota breach.
            assert client.get("/health", headers=headers).status_code == 200

    def test_disabled_middleware_never_blocks(self, tenant_and_user):
        app = FastAPI()

        @app.get("/echo")
        def echo():
            return {"ok": True}

        app.add_middleware(TenantQuotaMiddleware, quotas={"pro": 1}, enabled=False)
        with TestClient(app) as client:
            for _ in range(4):
                assert client.get("/echo", headers={"X-API-Key": "quota-test-key"}).status_code == 200


class TestQuotaConfiguration:
    def test_env_overrides_tier_ceilings(self, monkeypatch):
        monkeypatch.setenv("TENANT_QUOTA_PRO", "1234")
        mw = TenantQuotaMiddleware(FastAPI(), enabled=True)
        assert mw.quota_for("pro") == 1234
        assert mw.quota_for("enterprise") == DEFAULT_TENANT_QUOTAS["enterprise"]

    def test_unknown_tier_falls_back_to_free(self):
        mw = TenantQuotaMiddleware(FastAPI(), enabled=True)
        assert mw.quota_for("platinum") == DEFAULT_TENANT_QUOTAS["free"]
        assert mw.quota_for(None) == DEFAULT_TENANT_QUOTAS["free"]

    def test_disabled_under_pytest_by_default(self):
        """The suite fires hundreds of requests as one tenant; default-off there."""
        assert TenantQuotaMiddleware(FastAPI()).enabled is False

    def test_env_switch_beats_the_pytest_default(self, monkeypatch):
        monkeypatch.setenv("TENANT_QUOTA_ENABLED", "true")
        assert TenantQuotaMiddleware(FastAPI()).enabled is True
