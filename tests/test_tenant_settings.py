"""Settings are per organisation; shared infrastructure is the operator's.

Settings were one deployment-wide row, and any tenant's ADMIN could rewrite it:
set retention and PII redaction for every other tenant, or point the shared
object store — every tenant's raw logs, uploads and archives — at a bucket of
their choosing.
"""

import asyncio
import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from denoiser.api.platform_settings import (
    load_settings,
    load_tenant_settings,
    raw_log_storage_enabled,
    redaction_enabled,
    save_settings,
)
from denoiser.storage.db import AnalysisRun, SessionLocal, Tenant, TenantSetting, User, init_db

PLATFORM_TOKEN = "platform-operator-test-token"


@pytest.fixture
def orgs():
    """Two throwaway organisations, removed with their settings afterwards."""
    init_db()
    db = SessionLocal()
    made = []
    for _ in range(2):
        t = Tenant(name=f"settings-{uuid.uuid4().hex[:8]}")
        db.add(t)
        db.commit()
        db.refresh(t)
        made.append(t.id)
    original = load_settings(db)
    try:
        yield made
    finally:
        save_settings(original, db)
        db.query(TenantSetting).filter(TenantSetting.tenant_id.in_(made)).delete(synchronize_session=False)
        db.query(AnalysisRun).filter(AnalysisRun.tenant_id.in_(made)).delete(synchronize_session=False)
        db.query(Tenant).filter(Tenant.id.in_(made)).delete(synchronize_session=False)
        db.commit()
        db.close()


@pytest.fixture
def as_admin():
    """`as_admin(tenant_id)` -> a client signed in as that organisation's ADMIN."""
    from denoiser.api.auth import get_current_user
    from denoiser.api.main import app

    clients = []

    def make(tenant_id, role="ADMIN"):
        user = User(id=900000 + tenant_id, email=f"admin{tenant_id}@x.io", role=role, tenant_id=tenant_id)
        app.dependency_overrides[get_current_user] = lambda: user
        c = TestClient(app)
        clients.append(c)
        return c

    yield make
    app.dependency_overrides.clear()


class TestIsolation:
    def test_one_tenants_retention_is_not_anothers(self, orgs, as_admin):
        a, b = orgs
        b_before = load_tenant_settings(b)["retention_days"]

        assert as_admin(a).put("/settings", json={"retention_days": 9}).status_code == 200

        assert load_tenant_settings(a)["retention_days"] == 9
        assert load_tenant_settings(b)["retention_days"] == b_before

    def test_redaction_switch_applies_to_its_own_tenant_only(self, orgs, as_admin):
        a, b = orgs
        save_settings({**load_settings(), "redact_pii": True})
        as_admin(a).put("/settings", json={"redact_pii": False, "store_raw_logs": False})

        assert redaction_enabled(a) is False
        assert redaction_enabled(b) is True
        assert raw_log_storage_enabled(a) is False

    def test_each_admin_reads_back_their_own_values(self, orgs, as_admin):
        a, b = orgs
        as_admin(a).put("/settings", json={"retention_days": 11})
        as_admin(b).put("/settings", json={"retention_days": 22})

        assert as_admin(a).get("/settings").json()["retention_days"] == 11
        assert as_admin(b).get("/settings").json()["retention_days"] == 22

    def test_a_tenant_without_a_row_keeps_the_deployment_values(self, orgs):
        """The upgrade path: nothing changes for anyone until they save."""
        a, _ = orgs
        save_settings({**load_settings(), "retention_days": 41, "redact_pii": False})
        assert load_tenant_settings(a)["retention_days"] == 41
        assert redaction_enabled(a) is False

    def test_ingest_tenant_ids_arrive_as_strings(self, orgs, as_admin):
        a, _ = orgs
        as_admin(a).put("/settings", json={"redact_pii": False})
        assert redaction_enabled(str(a)) is False

    def test_viewer_cannot_change_settings(self, orgs, as_admin):
        a, _ = orgs
        assert as_admin(a, role="VIEWER").put("/settings", json={"retention_days": 3}).status_code == 403


class TestPlatformKeysAreTheOperators:
    def test_tenant_admin_cannot_repoint_the_shared_object_store(self, orgs, as_admin):
        a, _ = orgs
        res = as_admin(a).put("/settings", json={"s3_bucket": "attacker-bucket", "s3_endpoint": "https://evil.example"})
        assert res.status_code == 403
        assert "s3_bucket" in res.json()["detail"]
        assert load_settings().get("s3_bucket") != "attacker-bucket"

    def test_saving_the_page_with_platform_values_unchanged_is_fine(self, orgs, as_admin):
        a, _ = orgs
        client = as_admin(a)
        page = client.get("/settings").json()
        page.pop("platform_managed")
        assert client.put("/settings", json={**page, "retention_days": 12}).status_code == 200
        assert load_tenant_settings(a)["retention_days"] == 12

    def test_the_view_says_which_keys_are_read_only(self, orgs, as_admin):
        a, _ = orgs
        managed = as_admin(a).get("/settings").json()["platform_managed"]
        assert "s3_bucket" in managed and "retention_days" not in managed


class TestPlatformEndpoint:
    @pytest.fixture
    def operator(self, monkeypatch, orgs):
        from denoiser.api.main import app

        monkeypatch.setenv("SEMANTICOS_PLATFORM_TOKEN", PLATFORM_TOKEN)
        return TestClient(app), {"Authorization": f"Bearer {PLATFORM_TOKEN}"}

    def test_operator_sets_the_object_store(self, operator):
        client, auth = operator
        res = client.put("/platform/settings", json={"s3_bucket": "shared-archive"}, headers=auth)
        assert res.status_code == 200
        assert load_settings()["s3_bucket"] == "shared-archive"

    def test_secrets_stay_masked_for_the_operator_too(self, operator):
        from denoiser.api.routers_settings import SECRET_MASK

        client, auth = operator
        client.put("/platform/settings", json={"s3_secret_key": "real"}, headers=auth)
        assert client.get("/platform/settings", headers=auth).json()["s3_secret_key"] == SECRET_MASK
        assert load_settings()["s3_secret_key"] == "real"

    def test_operator_endpoint_does_not_rewrite_tenant_settings(self, operator):
        client, auth = operator
        res = client.put("/platform/settings", json={"retention_days": 1}, headers=auth)
        assert res.status_code == 400

    def test_requires_the_operator_token(self, operator):
        client, _ = operator
        assert client.get("/platform/settings").status_code == 401
        assert client.put("/platform/settings", json={"s3_bucket": "x"},
                          headers={"Authorization": "Bearer wrong"}).status_code == 401


class TestRetentionIsPerTenant:
    def test_cleanup_honours_each_tenants_own_window(self, orgs):
        from denoiser.api.platform_settings import save_tenant_settings
        from denoiser.api.scheduler import cleanup_database_records

        short, long = orgs
        save_tenant_settings(short, {"retention_days": 5})
        save_tenant_settings(long, {"retention_days": 30})

        ten_days_ago = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=10)
        db = SessionLocal()
        try:
            for tenant_id in orgs:
                db.add(AnalysisRun(id=f"run_{uuid.uuid4().hex[:8]}", tenant_id=tenant_id, created_at=ten_days_ago))
            db.commit()

            asyncio.run(cleanup_database_records())

            remaining = {r.tenant_id for r in db.query(AnalysisRun).filter(AnalysisRun.tenant_id.in_(orgs))}
            assert remaining == {long}
        finally:
            db.close()
