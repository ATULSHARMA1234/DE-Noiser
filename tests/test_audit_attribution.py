"""Audit middleware attributes mutating actions to the authenticated actor via
request.state, without re-decoding the JWT (audit finding L2)."""

import pytest
from fastapi.testclient import TestClient

from denoiser.api.auth import create_access_token, get_password_hash


@pytest.fixture(scope="module", autouse=True)
def _db():
    from denoiser.storage.db import init_db
    init_db()


@pytest.fixture
def client():
    from denoiser.api.main import app
    return TestClient(app)


def _make_user(email="audit-actor@bigcorp.com"):
    from denoiser.storage.db import SessionLocal, User
    db = SessionLocal()
    db.query(User).filter(User.email == email).delete()
    db.add(User(email=email, hashed_password=get_password_hash("pw-123456"),
                role="ANALYST", tenant_id=1, is_active=True))
    db.commit()
    uid = db.query(User).filter(User.email == email).first().id
    db.close()
    return email, uid


def _latest_audit_for(path):
    from denoiser.storage.db import AuditLog, SessionLocal
    db = SessionLocal()
    row = (
        db.query(AuditLog)
        .filter(AuditLog.resource_type == path)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .first()
    )
    result = (row.user_id, row.action) if row else None
    db.close()
    return result


def test_authenticated_post_is_attributed_to_actor(client):
    email, uid = _make_user()
    token = create_access_token(data={"sub": email})
    auth = {"Authorization": f"Bearer {token}"}

    # /auth/logout is an authenticated POST → goes through get_current_user,
    # which stamps request.state for the middleware.
    assert client.post("/auth/logout", headers=auth).status_code == 200

    attribution = _latest_audit_for("/auth/logout")
    assert attribution is not None, "no audit row written"
    user_id, action = attribution
    assert action == "POST"
    assert user_id == uid  # attributed to the real actor, not system-audit
