"""
Symmetric encryption for secrets held in the platform database.

Some values must be stored recoverably rather than hashed — an alert
destination URL has to be replayed verbatim at delivery time, so it cannot be a
one-way digest. Those values are encrypted at rest here, which means a database
dump, a replica, or a backup snapshot does not hand over live credentials. A
Slack or PagerDuty webhook URL *is* a bearer credential: anyone holding it can
post into that workspace.

Key material comes from ``SEMANTICOS_ENCRYPTION_KEY`` when set. When it is not,
the key is derived from the JWT signing secret via HKDF so that an existing
deployment gains encryption without a new required setting. Deriving rather than
reusing keeps the two purposes cryptographically separate: the derived key
cannot be used to mint tokens, and the signing key cannot decrypt stored
secrets.
"""

from __future__ import annotations

import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from denoiser.logging import get_logger

logger = get_logger(__name__)

_HKDF_INFO = b"semanticos.storage.secrets.v1"

# Marks a value this module produced, so a column that still holds a
# pre-encryption plaintext row is recognisable and can be read through
# unchanged instead of raising.
_PREFIX = "enc:v1:"


def _derive_key(material: str) -> bytes:
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(material.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    import os

    from denoiser.settings import get_settings

    explicit = os.getenv("SEMANTICOS_ENCRYPTION_KEY")
    if explicit:
        return Fernet(_derive_key(explicit))

    settings = get_settings()
    if settings.jwt_secret_key:
        return Fernet(_derive_key(settings.jwt_secret_key))

    # Only reachable in tests and local development: production boot already
    # refuses to start without a JWT secret.
    from denoiser.api.keys import get_keyring

    return Fernet(_derive_key(get_keyring().active.secret))


def reset_cache() -> None:
    """Drop the cached key. For tests that swap the environment."""
    _fernet.cache_clear()


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a value for storage. ``None`` and empty strings pass through."""
    if not plaintext:
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt(stored: str | None) -> str | None:
    """Recover a stored value.

    A value without the version prefix predates encryption and is returned
    as-is, so an upgrade does not strand existing rows.
    """
    if not stored:
        return stored
    if not stored.startswith(_PREFIX):
        return stored
    try:
        return _fernet().decrypt(stored[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        # Wrong key (rotated signing secret without re-encrypting, or a restored
        # backup from another environment). Surfacing None lets the caller treat
        # the destination as unusable rather than delivering to garbage.
        logger.error("Failed to decrypt stored secret — key mismatch")
        return None


def mask_url(url: str | None) -> str:
    """Render a URL safe to return over the API.

    Keeps the scheme, host and enough of the path to identify *which*
    destination this is, and drops the part that authenticates.
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return "••••••••"
        segments = [s for s in parts.path.split("/") if s]
        if not segments:
            return f"{parts.scheme}://{parts.netloc}"
        # First path segment is usually a stable route ("services", "v2"); the
        # trailing segments are the secret.
        head = segments[0]
        return f"{parts.scheme}://{parts.netloc}/{head}/••••••••"
    except Exception:
        return "••••••••"
