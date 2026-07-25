"""Credential rotation with an overlap window.

The JWT signing key already rotates through an overlap (see ``denoiser.api.keys``).
The other long-lived secrets did not: the tenant API key that customers paste
into log shippers had no rotation path at all, and the static ingest and SCIM
tokens were single values, so changing either broke every agent at the same
instant. In practice that means nobody rotates them.

Everything here follows the same shape as the keyring: a current secret, an
optional superseded secret that keeps working for a bounded window, and constant
-time comparison. Revoking immediately is always available — rotate with no
overlap — which is the right move for a suspected leak.
"""

from __future__ import annotations

import hmac
import os
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from denoiser.api.keys import read_secret
from denoiser.logging import get_logger
from denoiser.storage.db import Tenant
from denoiser.utils.time import utcnow

logger = get_logger(__name__)

# How long a superseded tenant key keeps working when no explicit window is
# given. Long enough to redeploy a fleet of shippers, short enough that a
# forgotten rotation does not leave a second valid key around indefinitely.
DEFAULT_OVERLAP_HOURS = 24

# Generated keys are URL-safe so they survive being pasted into config files,
# headers and environment variables without escaping.
_KEY_BYTES = 32


def generate_api_key(prefix: str = "sk") -> str:
    """A fresh tenant API key. The prefix makes leaked keys greppable."""
    return f"{prefix}_{secrets.token_urlsafe(_KEY_BYTES)}"


def secrets_match(presented: str | None, expected: str | None) -> bool:
    """Constant-time comparison that tolerates missing values."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)


def _overlap_active(tenant: Tenant, now: datetime | None = None) -> bool:
    now = now or utcnow()
    expires = tenant.api_key_previous_expires_at
    return bool(tenant.api_key_previous and expires and expires > now)


def tenant_for_api_key(db: Session, api_key: str | None, now: datetime | None = None) -> Tenant | None:
    """The tenant a key belongs to, accepting a superseded key during overlap.

    Returns None for an unknown key, and for a superseded key whose window has
    closed — an expired overlap must not authenticate anything.
    """
    if not api_key:
        return None

    now = now or utcnow()
    tenant = db.query(Tenant).filter(Tenant.api_key == api_key).first()
    if tenant is not None:
        return tenant

    candidate = db.query(Tenant).filter(Tenant.api_key_previous == api_key).first()
    if candidate is not None and _overlap_active(candidate, now):
        logger.info(
            "Tenant %s authenticated with its superseded API key; overlap ends %s",
            candidate.id, candidate.api_key_previous_expires_at,
        )
        return candidate
    return None


def rotate_tenant_api_key(
    db: Session,
    tenant: Tenant,
    overlap_hours: int = DEFAULT_OVERLAP_HOURS,
    new_key: str | None = None,
    now: datetime | None = None,
) -> str:
    """Issue a new API key for a tenant. Returns the new key (shown once).

    ``overlap_hours=0`` revokes the old key immediately, which is what a
    suspected leak calls for; any positive value keeps it working that long so
    shippers can be updated in sequence.
    """
    now = now or utcnow()
    previous = tenant.api_key

    tenant.api_key = new_key or generate_api_key()
    tenant.api_key_rotated_at = now
    if previous and overlap_hours > 0:
        tenant.api_key_previous = previous
        tenant.api_key_previous_expires_at = now + timedelta(hours=overlap_hours)
    else:
        tenant.api_key_previous = None
        tenant.api_key_previous_expires_at = None

    db.commit()
    db.refresh(tenant)
    logger.info(
        "Rotated API key for tenant %s (overlap %sh)",
        tenant.id, overlap_hours if previous else 0,
    )
    return tenant.api_key


def revoke_previous_api_key(db: Session, tenant: Tenant) -> bool:
    """End an overlap early. Returns whether a superseded key was in place."""
    had_previous = bool(tenant.api_key_previous)
    tenant.api_key_previous = None
    tenant.api_key_previous_expires_at = None
    db.commit()
    if had_previous:
        logger.info("Revoked the superseded API key for tenant %s", tenant.id)
    return had_previous


def _accepted_static_secrets(env_var: str) -> list[str]:
    """Current plus superseded values for an env/file-sourced shared secret.

    ``<VAR>_PREVIOUS`` is comma-separated, most recent first — the same
    convention the JWT keyring uses, so operators learn one rotation shape.
    """
    accepted: list[str] = []
    current = read_secret(env_var)
    if current:
        accepted.append(current)

    retired = read_secret(f"{env_var}_PREVIOUS") or ""
    accepted.extend(part.strip() for part in retired.split(",") if part.strip())
    return accepted


def matches_static_secret(presented: str | None, env_var: str) -> bool:
    """Whether a presented secret matches the current or a superseded value."""
    if not presented:
        return False
    return any(secrets_match(presented, accepted) for accepted in _accepted_static_secrets(env_var))


def static_secret_configured(env_var: str) -> bool:
    return bool(read_secret(env_var))


def describe_static_rotation(env_var: str) -> dict:
    """Non-sensitive rotation state, for the operator-facing status endpoint."""
    accepted = _accepted_static_secrets(env_var)
    return {
        "configured": bool(accepted),
        "accepted_values": len(accepted),
        "overlap_active": len(accepted) > 1,
        "source": "file" if os.getenv(f"{env_var}_FILE") else ("env" if os.getenv(env_var) else "unset"),
    }
