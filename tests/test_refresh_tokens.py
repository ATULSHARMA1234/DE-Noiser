"""Short-lived access tokens + rotating refresh tokens (audit finding L1)."""

import pytest
from fastapi.testclient import TestClient

from denoiser.api.auth import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
)


@pytest.fixture(scope="module", autouse=True)
def _db():
    from denoiser.storage.db import init_db
    init_db()


@pytest.fixture
def client():
    from denoiser.api.main import app
    return TestClient(app)


def _make_user(email="refresh-user@bigcorp.com", password="pw-123456"):
    from denoiser.storage.db import SessionLocal, User
    db = SessionLocal()
    db.query(User).filter(User.email == email).delete()
    db.add(User(email=email, hashed_password=get_password_hash(password),
                role="ANALYST", tenant_id=1, is_active=True))
    db.commit()
    db.close()
    return email, password


def test_login_returns_access_and_refresh(client):
    email, password = _make_user()
    res = client.post("/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["access_token"] != body["refresh_token"]


def test_refresh_rotates_and_returns_new_pair(client):
    email, password = _make_user()
    login = client.post("/auth/login", json={"email": email, "password": password}).json()
    old_refresh = login["refresh_token"]

    res = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert res.status_code == 200, res.text
    new = res.json()
    assert new["access_token"] and new["refresh_token"]
    assert new["refresh_token"] != old_refresh  # rotated


def test_refresh_token_is_single_use(client):
    email, password = _make_user()
    login = client.post("/auth/login", json={"email": email, "password": password}).json()
    refresh = login["refresh_token"]

    assert client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 200
    # Reusing the now-revoked refresh token fails (reuse/theft protection).
    assert client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 401


def test_new_access_token_works_as_credential(client):
    email, password = _make_user()
    login = client.post("/auth/login", json={"email": email, "password": password}).json()
    refreshed = client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]}).json()
    auth = {"Authorization": f"Bearer {refreshed['access_token']}"}
    assert client.get("/auth/me", headers=auth).status_code == 200


def test_refresh_token_rejected_as_access_credential(client):
    """A refresh token must never authenticate an API request."""
    email, _ = _make_user()
    refresh = create_refresh_token(data={"sub": email})
    auth = {"Authorization": f"Bearer {refresh}"}
    assert client.get("/auth/me", headers=auth).status_code == 401


def test_access_token_still_authenticates(client):
    email, _ = _make_user()
    access = create_access_token(data={"sub": email})
    auth = {"Authorization": f"Bearer {access}"}
    assert client.get("/auth/me", headers=auth).status_code == 200
