import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from denoiser.api.auth import get_password_hash, verify_password, create_access_token
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
    from denoiser.api.auth import SECRET_KEY, ALGORITHM
    
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
