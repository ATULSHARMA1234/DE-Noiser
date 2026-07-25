"""JWT signing keys, their sources, and rotation.

The signing secret used to be a single static environment variable read once at
import. That has no rotation story: changing it invalidates every outstanding
token at the same instant (every user is signed out, every refresh token dies),
so in practice nobody rotates it, and a secret that is never rotated is one
compromise away from permanent token forgery.

This module gives the platform the two things a rotation needs:

  - **Overlap.** ``JWT_SECRET_KEY`` signs new tokens; ``JWT_SECRET_KEY_PREVIOUS``
    (comma-separated, most recent first) is still *accepted* while tokens signed
    with it drain. Once the longest token lifetime has passed, the retired key
    can be dropped. Nobody is signed out by a rotation.
  - **A source that can change without a redeploy.** Any secret may be supplied
    as ``<VAR>_FILE`` pointing at a mounted file — the shape every secret
    manager already speaks (Kubernetes Secret projections, Vault Agent, the AWS
    and GCP secret CSI drivers). The file is re-read when its mtime changes, so
    an external rotator can roll the key under a running process.

Tokens carry a ``kid`` header derived from the key itself (a truncated SHA-256,
never the secret), so verification picks the right key directly instead of
trying each in turn. Tokens minted before this change carry no ``kid`` and are
verified by trying every key in the ring.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from denoiser.logging import get_logger

logger = get_logger(__name__)

# How long a cached keyring is trusted before the file sources are re-checked.
KEYRING_REFRESH_SECONDS = float(os.getenv("JWT_KEYRING_REFRESH_SECONDS", "30"))


def read_secret(env_var: str) -> str | None:
    """Read a secret from ``<env_var>_FILE`` if set, else from ``env_var``.

    The file form is what every secret manager mounts, and unlike an env var it
    can be updated in place under a running process.
    """
    path = os.getenv(f"{env_var}_FILE")
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
            if value:
                return value
            logger.warning("%s_FILE points at an empty file (%s)", env_var, path)
        except OSError as e:
            logger.error("Cannot read %s_FILE (%s): %s", env_var, path, e)
    return os.getenv(env_var) or None


def _secret_file_stamp(env_var: str) -> float | None:
    """Modification time of a secret's backing file, if it has one."""
    path = os.getenv(f"{env_var}_FILE")
    if not path:
        return None
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


@dataclass(frozen=True)
class SigningKey:
    """One HMAC signing secret and the ``kid`` that identifies it."""

    secret: str

    @property
    def kid(self) -> str:
        """A stable, non-reversible identifier for this key.

        Truncated SHA-256 of the secret: the same key always yields the same
        kid across replicas and restarts without coordination, and the kid
        reveals nothing usable about the secret.
        """
        return hashlib.sha256(self.secret.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class KeyRing:
    """The key that signs, plus the retired keys still accepted."""

    active: SigningKey
    retired: tuple[SigningKey, ...] = ()

    @property
    def all_keys(self) -> tuple[SigningKey, ...]:
        return (self.active, *self.retired)

    def by_kid(self, kid: str | None) -> SigningKey | None:
        if not kid:
            return None
        for key in self.all_keys:
            if key.kid == kid:
                return key
        return None

    def describe(self) -> dict:
        """Rotation state for the ops/readiness surface. No secrets."""
        return {
            "active_kid": self.active.kid,
            "retired_kids": [k.kid for k in self.retired],
            "accepts_retired_tokens": bool(self.retired),
        }


class _KeyringCache:
    """Process-wide keyring with a cheap staleness check.

    Re-reading the environment (and possibly a file) on every token operation
    would put a syscall in the hot path of every authenticated request, so the
    ring is cached and re-validated at most every ``KEYRING_REFRESH_SECONDS``,
    and then only rebuilt if a source actually changed.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ring: KeyRing | None = None
        self._fingerprint: tuple | None = None
        self._checked_at: float = 0.0

    @staticmethod
    def _source_fingerprint() -> tuple:
        return (
            os.getenv("JWT_SECRET_KEY_FILE"),
            os.getenv("JWT_SECRET_KEY"),
            os.getenv("JWT_SECRET_KEY_PREVIOUS_FILE"),
            os.getenv("JWT_SECRET_KEY_PREVIOUS"),
            _secret_file_stamp("JWT_SECRET_KEY"),
            _secret_file_stamp("JWT_SECRET_KEY_PREVIOUS"),
        )

    def get(self) -> KeyRing:
        now = time.monotonic()
        with self._lock:
            if self._ring is not None and (now - self._checked_at) < KEYRING_REFRESH_SECONDS:
                return self._ring

            fingerprint = self._source_fingerprint()
            self._checked_at = now
            if self._ring is not None and fingerprint == self._fingerprint:
                return self._ring

            ring = _build_keyring()
            if self._ring is not None and ring.active.kid != self._ring.active.kid:
                logger.warning(
                    "JWT signing key rotated: now signing with kid=%s, still accepting %s",
                    ring.active.kid, [k.kid for k in ring.retired] or "no retired keys",
                )
            self._ring = ring
            self._fingerprint = fingerprint
            return ring

    def reset(self) -> None:
        with self._lock:
            self._ring = None
            self._fingerprint = None
            self._checked_at = 0.0


_cache = _KeyringCache()

# Shipped in an old commit as a fallback; only ever acceptable under tests.
_TEST_ONLY_SECRET = "semantic-os-super-secure-production-secret-key-1234567890"


def _build_keyring() -> KeyRing:
    from denoiser.settings import is_testing

    active_secret = read_secret("JWT_SECRET_KEY")
    if not active_secret:
        if not is_testing():
            raise ValueError(
                "JWT_SECRET_KEY (or JWT_SECRET_KEY_FILE) is mandatory in non-test mode."
            )
        active_secret = _TEST_ONLY_SECRET

    retired_raw = read_secret("JWT_SECRET_KEY_PREVIOUS") or ""
    retired: list[SigningKey] = []
    seen = {active_secret}
    for part in retired_raw.split(","):
        secret = part.strip()
        if secret and secret not in seen:
            seen.add(secret)
            retired.append(SigningKey(secret))

    return KeyRing(active=SigningKey(active_secret), retired=tuple(retired))


def get_keyring() -> KeyRing:
    """The current keyring, re-reading rotated sources when they change."""
    return _cache.get()


def reset_keyring() -> None:
    """Drop the cached ring. For tests and for an explicit operator-forced reload."""
    _cache.reset()
