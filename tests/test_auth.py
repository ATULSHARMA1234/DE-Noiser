import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from denoiser.api.auth import create_access_token, get_password_hash, verify_password
from denoiser.storage.db import User


def test_password_hashing():
    """Test that password hashing and verification work properly."""
    pwd = "mysecretpassword"
    hashed = get_password_hash(pwd)
    assert hashed != pwd
    assert verify_password(pwd, hashed) is True
    assert verify_password("wrong", hashed) is False


def test_jwt_generation_and_decoding():
    """Test that access tokens can be issued and decoded successfully."""
    from jose import jwt

    from denoiser.api.auth import ALGORITHM, SECRET_KEY

    data = {"sub": "test@example.com"}
    token = create_access_token(data)

    decoded = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert decoded["sub"] == "test@example.com"
    assert "exp" in decoded


def test_auth_login_success(db_session: Session):
    """Test standard JSON auth login with correct credentials."""
    from denoiser.api.main import app

    with TestClient(app) as client:
        # 1. Create a dummy analyst user
        email = "analyst@semanticos.io"
        password = "analystpassword"

        # Clean up any existing user to ensure test runs successfully
        db_session.query(User).filter(User.email == email).delete()
        db_session.commit()

        user = User(
            email=email,
            hashed_password=get_password_hash(password),
            role="ANALYST"
        )
        db_session.add(user)
        db_session.commit()

        # 2. POST /auth/login
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200

        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == email
        assert data["user"]["role"] == "ANALYST"


def test_auth_login_invalid_password(db_session: Session):
    """Test auth login returns 401 when password is bad."""
    from denoiser.api.main import app
    with TestClient(app) as client:
        response = client.post("/auth/login", json={"email": "admin@semanticos.io", "password": "wrongpassword"})
        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]


@pytest.fixture
def db_session():
    """Fixture that yields a database session for clean mock testing."""
    from denoiser.storage.db import SessionLocal, init_db
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_user_deactivation_and_login_block(db_session: Session):
    from denoiser.api.main import app
    from denoiser.api.auth import get_password_hash

    with TestClient(app) as client:
        # Create an analyst user
        email = "deact_test@semanticos.io"
        password = "testpassword123"

        # Cleanup
        db_session.query(User).filter(User.email == email).delete()
        db_session.commit()

        user = User(
            email=email,
            hashed_password=get_password_hash(password),
            role="ANALYST",
            is_active=True
        )
        db_session.add(user)
        db_session.commit()

        # Login should succeed first
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200

        # Now deactivate user
        user.is_active = False
        db_session.commit()

        # Login should fail now
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 401
        assert "deactivated" in response.json()["detail"]


def test_admin_deactivate_endpoint(db_session: Session):
    from denoiser.api.main import app
    from denoiser.api.auth import get_password_hash

    with TestClient(app) as client:
        # Create admin and analyst
        admin_email = "admin_deact@semanticos.io"
        analyst_email = "analyst_deact@semanticos.io"
        pwd = "password123"

        db_session.query(User).filter(User.email.in_([admin_email, analyst_email])).delete()
        db_session.commit()

        admin_user = User(email=admin_email, hashed_password=get_password_hash(pwd), role="ADMIN", is_active=True)
        analyst_user = User(email=analyst_email, hashed_password=get_password_hash(pwd), role="ANALYST", is_active=True)
        db_session.add_all([admin_user, analyst_user])
        db_session.commit()

        # Log in as admin
        admin_login = client.post("/auth/login", json={"email": admin_email, "password": pwd})
        assert admin_login.status_code == 200
        admin_token = admin_login.json()["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Log in as analyst
        analyst_login = client.post("/auth/login", json={"email": analyst_email, "password": pwd})
        assert analyst_login.status_code == 200
        analyst_token = analyst_login.json()["access_token"]
        analyst_headers = {"Authorization": f"Bearer {analyst_token}"}

        # Analyst cannot deactivate user (should get 403)
        deact_by_analyst = client.put(f"/users/{analyst_user.id}/deactivate", headers=analyst_headers)
        assert deact_by_analyst.status_code == 403

        # Admin deactivates analyst
        deact_by_admin = client.put(f"/users/{analyst_user.id}/deactivate", headers=headers)
        assert deact_by_admin.status_code == 200
        assert deact_by_admin.json()["is_active"] is False

        # Admin cannot deactivate self
        deact_self = client.put(f"/users/{admin_user.id}/deactivate", headers=headers)
        assert deact_self.status_code == 400

        # Admin cannot deactivate system-audit user
        sys_audit = db_session.query(User).filter(User.email == "system-audit@semanticos.io").first()
        if sys_audit:
            deact_sys = client.put(f"/users/{sys_audit.id}/deactivate", headers=headers)
            assert deact_sys.status_code == 400


def test_audit_middleware_fallback(db_session: Session):
    from denoiser.api.main import app
    from denoiser.storage.db import AuditLog

    with TestClient(app) as client:
        # Perform a mutating action without authentication to trigger fallback in audit log
        response = client.put("/settings", json={"retention_days": 45})
        
        # Let's check the database for the newest audit log
        audit_log = db_session.query(AuditLog).order_by(AuditLog.id.desc()).first()
        assert audit_log is not None
        # Should fall back to system-audit user
        sys_audit_user = db_session.query(User).filter(User.email == "system-audit@semanticos.io").first()
        assert sys_audit_user is not None
        assert audit_log.user_id == sys_audit_user.id


def test_audit_middleware_attributes_authenticated_actor(db_session: Session):
    """An authenticated mutating action must be attributed to the actual actor, not system-audit."""
    from denoiser.api.main import app
    from denoiser.storage.db import AuditLog

    email = "audit-actor@semanticos.io"
    password = "auditpassword"
    db_session.query(User).filter(User.email == email).delete()
    db_session.commit()
    user = User(email=email, hashed_password=get_password_hash(password), role="ADMIN")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    with TestClient(app) as client:
        token = client.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]
        client.put("/settings", json={"retention_days": 33}, headers={"Authorization": f"Bearer {token}"})

        audit_log = db_session.query(AuditLog).order_by(AuditLog.id.desc()).first()
        assert audit_log is not None
        assert audit_log.user_id == user.id
