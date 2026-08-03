import os
import secrets

# Secret key and algorithm for JWT
import sys
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from denoiser.api.keys import get_keyring
from denoiser.storage.db import User, get_db

is_testing = "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv) or "PYTEST_CURRENT_TEST" in os.environ
ALGORITHM = "HS256"
# Tokens are signed with the keyring's active key and verified against every key
# still in the ring, so a rotation drains outstanding tokens instead of signing
# everyone out. Raises at import in non-test mode when no secret is configured.
_keyring = get_keyring()


def __getattr__(name: str):
    """``SECRET_KEY`` stays readable as the *current* active signing secret.

    It is a module attribute rather than a constant so that a key rotated at
    runtime is reflected here too, instead of handing callers a stale secret.
    """
    if name == "SECRET_KEY":
        return get_keyring().active.secret
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def encode_token(claims: dict) -> str:
    """Sign a token with the active key, stamping its kid into the header."""
    key = get_keyring().active
    return jwt.encode(claims, key.secret, algorithm=ALGORITHM, headers={"kid": key.kid})


def decode_token(token: str) -> dict:
    """Verify a token against the keyring. Raises ``JWTError`` if no key matches.

    The ``kid`` header selects the key directly. Tokens minted before kid
    stamping (or by a replica mid-rotation) carry none, so every key in the ring
    is tried before the token is rejected.
    """
    ring = get_keyring()
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except JWTError:
        kid = None

    candidates = [named] if (named := ring.by_kid(kid)) else list(ring.all_keys)
    last_error: JWTError | None = None
    for key in candidates:
        try:
            return jwt.decode(token, key.secret, algorithms=[ALGORITHM])
        except JWTError as e:
            last_error = e
    raise last_error or JWTError("Token could not be verified with any active key")


# Short-lived access tokens limit the blast radius of a leaked token; a
# longer-lived, single-use refresh token (rotated on every use) keeps sessions
# alive without a 24h bearer floating around. Both are env-tunable.
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_MINUTES = int(os.getenv("REFRESH_TOKEN_EXPIRE_MINUTES", str(60 * 24 * 7)))  # 7 days

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


#: A hash of a value nobody knows, verified against when the submitted email
#: matches no account. Without it, "no such user" returns in microseconds while
#: a real address costs a full bcrypt verify, and the difference enumerates the
#: user directory. Generated per process so it cannot be set as a password.
DUMMY_PASSWORD_HASH = get_password_hash(secrets.token_urlsafe(32))


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Generate a short-lived JWT access token with an expiration timestamp."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

    # Every token carries a unique jti so it can be individually revoked, and a
    # type so a refresh token can never be presented as an access token.
    to_encode.update({"exp": expire, "jti": uuid.uuid4().hex, "type": "access"})
    return encode_token(to_encode)


def create_refresh_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Generate a longer-lived, single-use refresh token (rotated on each use)."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "jti": uuid.uuid4().hex, "type": "refresh"})
    return encode_token(to_encode)


def issue_token_pair(sub: str) -> dict:
    """The standard login/refresh payload: a fresh access + refresh token."""
    return {
        "access_token": create_access_token(data={"sub": sub}),
        "refresh_token": create_refresh_token(data={"sub": sub}),
        "token_type": "bearer",
    }


def rotate_refresh_token(refresh_token: str, db: Session) -> tuple[dict, User]:
    """Validate a refresh token, revoke it (single-use), and mint a new pair.

    Returns ``(token_pair, user)``. Raises HTTPException(401) on any
    invalid/expired/revoked/reused token or a deactivated user. Rotation means a
    stolen refresh token is usable at most once before the legitimate client's
    next refresh invalidates it.
    """
    from denoiser.storage.db import RevokedToken

    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(refresh_token)
    except JWTError:
        raise invalid

    if payload.get("type") != "refresh":
        raise invalid
    jti, sub, exp = payload.get("jti"), payload.get("sub"), payload.get("exp")
    if not jti or not sub or not exp:
        raise invalid

    # Single-use: a refresh token already spent (or explicitly revoked) is dead.
    if db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
        raise invalid

    user = db.query(User).filter(User.email == sub).first()
    if user is None or not getattr(user, "is_active", True):
        raise invalid

    # Revoke the presented refresh token before issuing the next one.
    db.add(RevokedToken(jti=jti, expires_at=datetime.fromtimestamp(exp, UTC)))
    db.commit()
    return issue_token_pair(sub), user


def revoke_token(token: str, db: Session) -> bool:
    """Invalidate a token by recording its jti until it would expire.

    Returns True if the token was valid and is now revoked (or already was).
    """
    from denoiser.storage.db import RevokedToken

    try:
        payload = decode_token(token)
    except JWTError:
        return False

    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp:
        return False

    if not db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
        db.add(RevokedToken(jti=jti, expires_at=datetime.fromtimestamp(exp, UTC)))
        db.commit()
    return True


def get_current_user(
    request: Request = None,  # type: ignore[assignment]
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
        # Browsers carry the session in an httpOnly cookie, which JavaScript
        # cannot read and therefore cannot put in an Authorization header.
        # Programmatic clients keep using the header.
        from denoiser.api.cookies import token_from_cookie

        token = token_from_cookie(request)
    if not token:
        raise credentials_exception

    try:
        payload = decode_token(token)
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        # A refresh token must never be accepted as an API access credential.
        if payload.get("type") == "refresh":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Reject tokens that have been explicitly revoked (logout / forced sign-out).
    jti = payload.get("jti")
    if jti:
        from denoiser.storage.db import RevokedToken
        if db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
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
    # Stamp the resolved identity onto the request so the audit middleware can
    # attribute the action without re-decoding the JWT or re-querying the user.
    if request is not None:
        request.state.audit_user_id = user.id
        request.state.audit_user_email = user.email
        # The audit row is filtered by tenant on read, so it has to carry one.
        request.state.audit_tenant_id = user.tenant_id
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
    from denoiser.api.credentials import matches_static_secret, tenant_for_api_key
    from denoiser.storage.db import Tenant
    if api_key:
        # Accepts a key that is mid-rotation, so a fleet of shippers can be
        # updated in sequence rather than all at the instant of rotation.
        tenant = tenant_for_api_key(db, api_key)
        if tenant:
            return tenant.id
        # Optional static key for unattended ingest. No hardcoded production
        # default — it must be set via INGEST_API_KEY (a dev default is allowed
        # only under tests so the suite can exercise the path).
        # INGEST_API_KEY_PREVIOUS holds superseded values during a rotation.
        matched_static = matches_static_secret(api_key, "INGEST_API_KEY")
        if not matched_static and is_testing and not os.getenv("INGEST_API_KEY"):
            matched_static = api_key == "semanticos-ingest-key-123"
        if matched_static:
            # Resolve to a real tenant. Returning the literal "default_tenant"
            # writes rows under a tenant id that no user can ever hold, so
            # everything ingested with the static key was orphaned: stored, but
            # invisible to every logged-in user (who carry a numeric tenant_id).
            default_tenant = db.query(Tenant).order_by(Tenant.id).first()
            if default_tenant:
                return str(default_tenant.id)
            return "default_tenant"

    if token:
        try:
            # Keyword arguments: get_current_user takes (request, token, db), so
            # a positional (token, db) call bound the token to `request` and the
            # session to `token` — every JWT-authenticated ingest failed with
            # "Invalid API Key or JWT token", leaving X-API-Key the only way in.
            user = get_current_user(request=None, token=token, db=db)
            return user.tenant_id
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="Invalid API Key or JWT token")
