"""Tests for the integrations CRUD API.

The UI's "Configure" button had nothing to call — there was no update endpoint —
and the credential typed into the connect dialog was discarded. These cover the
update path and the credential handling that came with it: secrets are stored,
never echoed back, and a resubmitted mask does not overwrite the real token.
"""

import pytest
from fastapi.testclient import TestClient

from denoiser.api.auth import create_access_token, get_password_hash
from denoiser.api.integrations import MASK
from denoiser.storage.db import Integration as DBIntegration
from denoiser.storage.db import SessionLocal, Tenant, User, init_db

TOKEN = "ghp_a_real_looking_token_value"


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
    try:
        email = "integrations-admin@semanticos.io"
        db.query(User).filter(User.email == email).delete()
        db.commit()
        tenant = db.query(Tenant).order_by(Tenant.id).first()
        db.add(User(
            email=email,
            hashed_password=get_password_hash("password123"),
            role="ADMIN",
            tenant_id=tenant.id if tenant else 1,
            is_active=True,
        ))
        db.commit()
        yield {"Authorization": f"Bearer {create_access_token(data={'sub': email})}"}
    finally:
        db.query(User).filter(User.email == "integrations-admin@semanticos.io").delete()
        db.commit()
        db.close()


@pytest.fixture
def integration(client, admin_auth):
    res = client.post("/integrations", headers=admin_auth, json={
        "provider": "github",
        "name": "GitHub Connection",
        "config": {"api_key": TOKEN, "repo": "acme/payments"},
    })
    assert res.status_code == 200, res.text
    created = res.json()
    yield created
    client.delete(f"/integrations/{created['id']}", headers=admin_auth)


class TestCredentialHandling:
    def test_create_does_not_echo_the_credential(self, integration):
        assert integration["config"]["api_key"] == MASK
        assert integration["config"]["repo"] == "acme/payments"

    def test_stored_credential_is_the_real_one(self, integration):
        db = SessionLocal()
        try:
            row = db.query(DBIntegration).filter(DBIntegration.id == integration["id"]).first()
            assert row.config["api_key"] == TOKEN
        finally:
            db.close()

    def test_list_masks_the_credential(self, client, admin_auth, integration):
        listed = client.get("/integrations", headers=admin_auth).json()
        mine = next(i for i in listed if i["id"] == integration["id"])
        assert mine["config"]["api_key"] == MASK


class TestUpdate:
    def test_configure_updates_the_credential(self, client, admin_auth, integration):
        res = client.put(
            f"/integrations/{integration['id']}",
            headers=admin_auth,
            json={"config": {"api_key": "ghp_rotated_token"}},
        )
        assert res.status_code == 200
        assert res.json()["config"]["api_key"] == MASK

        db = SessionLocal()
        try:
            row = db.query(DBIntegration).filter(DBIntegration.id == integration["id"]).first()
            assert row.config["api_key"] == "ghp_rotated_token"
            assert row.config["repo"] == "acme/payments"  # untouched keys survive
        finally:
            db.close()

    def test_resubmitting_the_mask_keeps_the_stored_secret(self, client, admin_auth, integration):
        """The UI renders the mask; sending it back must not erase the token."""
        client.put(
            f"/integrations/{integration['id']}",
            headers=admin_auth,
            json={"config": {"api_key": MASK, "repo": "acme/billing"}},
        )
        db = SessionLocal()
        try:
            row = db.query(DBIntegration).filter(DBIntegration.id == integration["id"]).first()
            assert row.config["api_key"] == TOKEN
            assert row.config["repo"] == "acme/billing"
        finally:
            db.close()

    def test_rename_and_disable(self, client, admin_auth, integration):
        res = client.put(
            f"/integrations/{integration['id']}",
            headers=admin_auth,
            json={"name": "GitHub (prod)", "enabled": False},
        )
        assert res.status_code == 200
        assert res.json()["name"] == "GitHub (prod)"
        assert res.json()["enabled"] is False

    def test_unknown_integration_is_404(self, client, admin_auth):
        assert client.put("/integrations/999999", headers=admin_auth, json={"name": "x"}).status_code == 404

    def test_update_requires_admin(self, client, integration):
        db = SessionLocal()
        try:
            email = "integrations-viewer@semanticos.io"
            db.query(User).filter(User.email == email).delete()
            db.commit()
            tenant = db.query(Tenant).order_by(Tenant.id).first()
            db.add(User(
                email=email, hashed_password=get_password_hash("password123"),
                role="VIEWER", tenant_id=tenant.id if tenant else 1, is_active=True,
            ))
            db.commit()
            headers = {"Authorization": f"Bearer {create_access_token(data={'sub': email})}"}
            res = client.put(f"/integrations/{integration['id']}", headers=headers, json={"name": "nope"})
            assert res.status_code == 403
        finally:
            db.query(User).filter(User.email == "integrations-viewer@semanticos.io").delete()
            db.commit()
            db.close()
