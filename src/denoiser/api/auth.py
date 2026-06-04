import os

# Secret key and algorithm for JWT
import sys
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from denoiser.storage.db import User, get_db

is_testing = "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv) or "PYTEST_CURRENT_TEST" in os.environ
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    if is_testing:
        SECRET_KEY = "semantic-os-super-secure-production-secret-key-1234567890"
    else:
        raise ValueError("JWT_SECRET_KEY environment variable is mandatory in non-test mode.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain text password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Generate bcrypt hash for a plain text password."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Generate a JWT access token containing claims and an expiration timestamp."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + expires_delta if expires_delta else datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """
    FastAPI dependency that extracts and validates the Bearer JWT from headers.
    Returns the authenticated User model or raises 401.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    if not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(allowed_roles: list[str]):
    """
    Dependency generator for Role-Based Access Control (RBAC).
    Raises 403 Forbidden if the user's role is not allowed.
    """
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions to access this resource"
            )
        return current_user
    return dependency


from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_ingest_auth(
    api_key: str | None = Depends(api_key_header),
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> str:
    """Allow ingest if X-API-Key header matches static config, or if a valid JWT is present. Returns tenant_id."""
    from denoiser.storage.db import Tenant
    if api_key:
        tenant = db.query(Tenant).filter(Tenant.api_key == api_key).first()
        if tenant:
            return tenant.id
        # Optional static key for unattended ingest. No hardcoded production
        # default — it must be set via INGEST_API_KEY (a dev default is allowed
        # only under tests so the suite can exercise the path).
        static_key = os.getenv("INGEST_API_KEY")
        if not static_key and is_testing:
            static_key = "semanticos-ingest-key-123"
        if static_key and api_key == static_key:
            return "default_tenant"

    if token:
        try:
            user = get_current_user(token, db)
            return user.tenant_id
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="Invalid API Key or JWT token")
