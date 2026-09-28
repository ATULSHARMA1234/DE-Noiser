"""Settings that every replica can see, and deployment markers that name the
right service.

Settings lived in `data/settings.json` on the API pod's own disk, so a second
replica could not see what the first one saved and both could clobber each
other — the API was stateful for no good reason. Separately, GitHub deployment
sync derived the service name from the repo, which merges every service in a
monorepo into one marker series.
"""

import json

import pytest
from fastapi.testclient import TestClient

from denoiser.api.auth import create_access_token, get_password_hash
from denoiser.storage.db import Integration as DBIntegration
from denoiser.storage.db import PlatformSetting, SessionLocal, Tenant, User, init_db


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
    email = "settings-admin@semanticos.io"
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


class TestSettingsAreShared:
    def test_saved_settings_land_in_the_database(self):
        from denoiser.api.platform_settings import (
            SETTINGS_ROW_ID,
            load_settings,
            save_settings,
        )

        save_settings({**load_settings(), "retention_days": 41})

        db = SessionLocal()
        try:
            row = db.query(PlatformSetting).filter(PlatformSetting.id == SETTINGS_ROW_ID).first()
            assert row is not None, "settings never reached the shared store"
            assert row.data["retention_days"] == 41
        finally:
            db.close()

    def test_a_second_process_reads_what_the_first_wrote(self):
        """This is the property a local file could not provide."""
        from denoiser.api.platform_settings import load_settings, save_settings

        save_settings({**load_settings(), "sampling_threshold": 12345})
        # A fresh session stands in for another replica.
        other_replica = SessionLocal()
        try:
            assert load_settings(db=other_replica)["sampling_threshold"] == 12345
        finally:
            other_replica.close()

    def test_new_defaults_appear_without_a_migration(self):
        from denoiser.api.platform_settings import DEFAULT_SETTINGS, load_settings, save_settings

        save_settings({"retention_days": 9})  # a document saved before a key existed
        loaded = load_settings()
        assert loaded["retention_days"] == 9
        assert loaded["redact_pii"] == DEFAULT_SETTINGS["redact_pii"]

    def test_legacy_file_is_imported_once(self, tmp_path, monkeypatch):
        from denoiser.api import platform_settings

        monkeypatch.setenv("SEMANTICOS_DATA_DIR", str(tmp_path))
        (tmp_path / "settings.json").write_text(json.dumps({"retention_days": 77}))

        db = SessionLocal()
        try:
            db.query(PlatformSetting).delete()
            db.commit()
            assert platform_settings.load_settings(db=db)["retention_days"] == 77
            row = db.query(PlatformSetting).first()
            assert row.data["retention_days"] == 77
        finally:
            db.query(PlatformSetting).delete()
            db.commit()
            db.close()

    def test_settings_endpoint_round_trips(self, client, admin_auth):
        client.put("/settings", headers=admin_auth, json={"retention_days": 33})
        assert client.get("/settings", headers=admin_auth).json()["retention_days"] == 33

    def test_retention_job_reads_the_shared_settings(self):
        from denoiser.api.platform_settings import load_settings, save_settings
        from denoiser.api.scheduler import get_retention_days

        save_settings({**load_settings(), "retention_days": 23})
        assert get_retention_days() == 23


class TestDeploymentServiceMapping:
    @pytest.fixture
    def github(self, client, admin_auth):
        res = client.post("/integrations", headers=admin_auth, json={
            "provider": "github", "name": "GitHub",
            "config": {"api_key": "ghp_x", "repo": "acme/platform"},
        })
        created = res.json()
        yield created
        client.delete(f"/integrations/{created['id']}", headers=admin_auth)

    def _stub_sync(self, monkeypatch, deployments):
        from denoiser.integrations.github import GitHubIntegration

        monkeypatch.setattr(GitHubIntegration, "sync_metadata", lambda self: {
            "provider": "GitHub", "repo": "acme/platform", "default_branch": "main",
            "deployments": deployments, "latest_release": {"tag": "v1"},
            "synced_at": "2026-07-26T00:00:00Z",
        })

    def _cleanup(self, versions):
        from denoiser.storage.db import DeploymentMarker

        db = SessionLocal()
        try:
            db.query(DeploymentMarker).filter(DeploymentMarker.version.in_(versions)).delete(
                synchronize_session=False
            )
            db.commit()
        finally:
            db.close()

    def test_service_defaults_to_the_repo_name(self, client, admin_auth, github, monkeypatch):
        self._stub_sync(monkeypatch, [
            {"sha": "aaaaaaaaaaaa1111", "ref": "main", "environment": "production",
             "created_at": "2026-07-20T10:00:00Z"},
        ])
        body = client.post(f"/integrations/{github['id']}/sync", headers=admin_auth).json()
        assert body["service"] == "platform"
        self._cleanup(["aaaaaaaaaaaa"])

    def test_configured_service_overrides_the_repo_name(self, client, admin_auth, github, monkeypatch):
        """A monorepo would otherwise collapse every service into one series."""
        from denoiser.storage.db import DeploymentMarker

        client.put(f"/integrations/{github['id']}", headers=admin_auth,
                   json={"config": {"service": "payments"}})
        self._stub_sync(monkeypatch, [
            {"sha": "bbbbbbbbbbbb2222", "ref": "main", "environment": "production",
             "created_at": "2026-07-20T10:00:00Z"},
        ])
        body = client.post(f"/integrations/{github['id']}/sync", headers=admin_auth).json()
        assert body["service"] == "payments"

        db = SessionLocal()
        try:
            marker = db.query(DeploymentMarker).filter(
                DeploymentMarker.version == "bbbbbbbbbbbb"
            ).first()
            assert marker.service == "payments"
        finally:
            db.close()
        self._cleanup(["bbbbbbbbbbbb"])

    def test_per_environment_mapping_is_honoured(self, client, admin_auth, github, monkeypatch):
        from denoiser.storage.db import DeploymentMarker

        client.put(f"/integrations/{github['id']}", headers=admin_auth, json={
            "config": {"service": "default-svc",
                       "service_by_environment": {"staging": "checkout-staging"}},
        })
        self._stub_sync(monkeypatch, [
            {"sha": "cccccccccccc3333", "ref": "main", "environment": "staging",
             "created_at": "2026-07-20T10:00:00Z"},
            {"sha": "dddddddddddd4444", "ref": "main", "environment": "production",
             "created_at": "2026-07-21T10:00:00Z"},
        ])
        client.post(f"/integrations/{github['id']}/sync", headers=admin_auth)

        db = SessionLocal()
        try:
            by_version = {
                m.version: m.service
                for m in db.query(DeploymentMarker).filter(
                    DeploymentMarker.version.in_(["cccccccccccc", "dddddddddddd"])
                ).all()
            }
            assert by_version["cccccccccccc"] == "checkout-staging"
            assert by_version["dddddddddddd"] == "default-svc"
        finally:
            db.close()
        self._cleanup(["cccccccccccc", "dddddddddddd"])

    def test_service_survives_a_credential_only_edit(self, client, admin_auth, github):
        client.put(f"/integrations/{github['id']}", headers=admin_auth,
                   json={"config": {"service": "billing"}})
        client.put(f"/integrations/{github['id']}", headers=admin_auth,
                   json={"config": {"api_key": "ghp_rotated"}})

        db = SessionLocal()
        try:
            row = db.query(DBIntegration).filter(DBIntegration.id == github["id"]).first()
            assert row.config["service"] == "billing"
            assert row.config["api_key"] == "ghp_rotated"
        finally:
            db.close()
