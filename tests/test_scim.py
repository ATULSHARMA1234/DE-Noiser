"""SCIM 2.0 provisioning: create, list-by-filter, deactivate, deprovision, and
group membership. Proves an IdP can manage the user lifecycle automatically."""

import os

import pytest
from fastapi.testclient import TestClient

SCIM_TOKEN = "test-scim-token-abc"


@pytest.fixture(scope="module", autouse=True)
def _configure_scim():
    os.environ["SCIM_BEARER_TOKEN"] = SCIM_TOKEN
    from denoiser.settings import reload_settings
    reload_settings()
    from denoiser.storage.db import init_db
    init_db()
    yield
    del os.environ["SCIM_BEARER_TOKEN"]
    reload_settings()


@pytest.fixture
def client():
    from denoiser.api.main import app
    return TestClient(app)


def _h(token=SCIM_TOKEN):
    return {"Authorization": f"Bearer {token}"}


class TestScimAuth:
    def test_rejects_missing_token(self, client):
        assert client.get("/scim/v2/Users").status_code == 401

    def test_rejects_wrong_token(self, client):
        assert client.get("/scim/v2/Users", headers=_h("nope")).status_code == 401


class TestScimUserLifecycle:
    def test_create_find_deactivate_deprovision(self, client):
        email = "scim-user@bigcorp.com"
        # Idempotency guard: remove if a previous run left it.
        from denoiser.storage.db import SessionLocal, User
        db = SessionLocal()
        db.query(User).filter(User.email == email).delete()
        db.commit()
        db.close()

        # Create
        res = client.post("/scim/v2/Users", headers=_h(), json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": email,
            "externalId": "idp|abc123",
            "active": True,
            "emails": [{"value": email, "primary": True}],
        })
        assert res.status_code == 201
        uid = res.json()["id"]
        assert res.json()["active"] is True

        # Find by SCIM filter (what an IdP does before creating).
        res = client.get(f'/scim/v2/Users?filter=userName eq "{email}"', headers=_h())
        assert res.json()["totalResults"] == 1

        # Deactivate via PATCH — the user can no longer authenticate.
        res = client.patch(f"/scim/v2/Users/{uid}", headers=_h(), json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        })
        assert res.json()["active"] is False

        db = SessionLocal()
        assert db.query(User).filter(User.id == int(uid)).first().is_active is False
        db.close()

        # De-provision (DELETE) — soft, keeps audit history.
        assert client.delete(f"/scim/v2/Users/{uid}", headers=_h()).status_code == 204

    def test_duplicate_create_conflicts(self, client):
        email = "scim-dupe@bigcorp.com"
        from denoiser.storage.db import SessionLocal, User
        db = SessionLocal()
        db.query(User).filter(User.email == email).delete()
        db.commit()
        db.close()
        payload = {"userName": email, "emails": [{"value": email, "primary": True}]}
        assert client.post("/scim/v2/Users", headers=_h(), json=payload).status_code == 201
        assert client.post("/scim/v2/Users", headers=_h(), json=payload).status_code == 409


class TestScimGroups:
    def test_group_membership_updates_user_teams(self, client):
        from denoiser.storage.db import SessionLocal, Team, User
        db = SessionLocal()
        db.query(User).filter(User.email == "team-member@bigcorp.com").delete()
        db.query(Team).filter(Team.name == "Platform SRE").delete()
        db.commit()
        db.close()

        u = client.post("/scim/v2/Users", headers=_h(), json={
            "userName": "team-member@bigcorp.com",
            "emails": [{"value": "team-member@bigcorp.com", "primary": True}],
        }).json()

        g = client.post("/scim/v2/Groups", headers=_h(), json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Platform SRE",
            "externalId": "grp|sre",
        }).json()

        # Add the user to the group.
        res = client.patch(f"/scim/v2/Groups/{g['id']}", headers=_h(), json={
            "Operations": [{"op": "add", "path": "members", "value": [{"value": u["id"]}]}],
        })
        assert any(m["value"] == u["id"] for m in res.json()["members"])

        # The membership is mirrored onto the user's teams.
        me = client.get(f"/scim/v2/Users/{u['id']}", headers=_h()).json()
        assert me["userName"] == "team-member@bigcorp.com"
        from denoiser.storage.db import SessionLocal, User
        db = SessionLocal()
        assert "Platform SRE" in (db.query(User).filter(User.id == int(u["id"])).first().teams or [])
        db.close()
