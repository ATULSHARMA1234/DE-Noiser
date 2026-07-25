"""Tests for JWT signing-key rotation.

A single static secret has no rotation story: changing it invalidates every
outstanding token at once. These cover the overlap window (retired keys verify
but never sign), file-backed secrets that an external rotator can update under a
running process, and the production checks that catch a botched rotation.
"""

import pytest
from jose import JWTError, jwt

from denoiser.api import keys as keys_module
from denoiser.api.auth import ALGORITHM, create_access_token, decode_token
from denoiser.api.keys import KeyRing, SigningKey, get_keyring, read_secret, reset_keyring
from denoiser.settings import KNOWN_INSECURE_JWT_SECRET, InfraSettings, validate_for_production

OLD_KEY = "old-secret-key-that-is-long-enough-for-production-1234"
NEW_KEY = "new-secret-key-that-is-long-enough-for-production-5678"


@pytest.fixture(autouse=True)
def clean_keyring():
    reset_keyring()
    yield
    reset_keyring()


@pytest.fixture
def no_refresh_delay(monkeypatch):
    """Re-read key sources on every call so a rotation is visible immediately."""
    monkeypatch.setattr(keys_module, "KEYRING_REFRESH_SECONDS", 0.0)


class TestKeyIdentity:
    def test_kid_is_stable_and_does_not_leak_the_secret(self):
        key = SigningKey(NEW_KEY)
        assert key.kid == SigningKey(NEW_KEY).kid
        assert key.kid != SigningKey(OLD_KEY).kid
        assert NEW_KEY not in key.kid
        assert len(key.kid) == 16

    def test_describe_reports_rotation_state_without_secrets(self):
        ring = KeyRing(active=SigningKey(NEW_KEY), retired=(SigningKey(OLD_KEY),))
        described = ring.describe()
        assert described["active_kid"] == SigningKey(NEW_KEY).kid
        assert described["retired_kids"] == [SigningKey(OLD_KEY).kid]
        assert described["accepts_retired_tokens"] is True
        assert NEW_KEY not in str(described) and OLD_KEY not in str(described)


class TestRotationOverlap:
    def test_token_signed_with_retired_key_still_verifies(self, monkeypatch, no_refresh_delay):
        # Before the rotation: OLD_KEY is active and mints a token.
        monkeypatch.setenv("JWT_SECRET_KEY", OLD_KEY)
        monkeypatch.delenv("JWT_SECRET_KEY_PREVIOUS", raising=False)
        reset_keyring()
        token = create_access_token(data={"sub": "user@semanticos.io"})

        # After the rotation: NEW_KEY signs, OLD_KEY is retired but accepted.
        monkeypatch.setenv("JWT_SECRET_KEY", NEW_KEY)
        monkeypatch.setenv("JWT_SECRET_KEY_PREVIOUS", OLD_KEY)
        reset_keyring()

        assert decode_token(token)["sub"] == "user@semanticos.io"
        # ...and new tokens are signed with the new key, not the retired one.
        fresh = create_access_token(data={"sub": "user@semanticos.io"})
        assert jwt.decode(fresh, NEW_KEY, algorithms=[ALGORITHM])["sub"] == "user@semanticos.io"
        with pytest.raises(JWTError):
            jwt.decode(fresh, OLD_KEY, algorithms=[ALGORITHM])

    def test_token_is_rejected_once_its_key_leaves_the_ring(self, monkeypatch, no_refresh_delay):
        monkeypatch.setenv("JWT_SECRET_KEY", OLD_KEY)
        monkeypatch.delenv("JWT_SECRET_KEY_PREVIOUS", raising=False)
        reset_keyring()
        token = create_access_token(data={"sub": "user@semanticos.io"})

        # The retirement window has closed: OLD_KEY is gone entirely.
        monkeypatch.setenv("JWT_SECRET_KEY", NEW_KEY)
        monkeypatch.delenv("JWT_SECRET_KEY_PREVIOUS", raising=False)
        reset_keyring()

        with pytest.raises(JWTError):
            decode_token(token)

    def test_multiple_retired_keys_are_all_accepted(self, monkeypatch, no_refresh_delay):
        third = "third-secret-key-long-enough-for-production-abcdefgh"
        monkeypatch.setenv("JWT_SECRET_KEY", OLD_KEY)
        monkeypatch.delenv("JWT_SECRET_KEY_PREVIOUS", raising=False)
        reset_keyring()
        oldest_token = create_access_token(data={"sub": "a@semanticos.io"})

        monkeypatch.setenv("JWT_SECRET_KEY", third)
        monkeypatch.setenv("JWT_SECRET_KEY_PREVIOUS", OLD_KEY)
        reset_keyring()
        middle_token = create_access_token(data={"sub": "b@semanticos.io"})

        monkeypatch.setenv("JWT_SECRET_KEY", NEW_KEY)
        monkeypatch.setenv("JWT_SECRET_KEY_PREVIOUS", f"{third}, {OLD_KEY}")
        reset_keyring()

        assert decode_token(oldest_token)["sub"] == "a@semanticos.io"
        assert decode_token(middle_token)["sub"] == "b@semanticos.io"

    def test_unsigned_and_garbage_tokens_are_rejected(self, monkeypatch, no_refresh_delay):
        monkeypatch.setenv("JWT_SECRET_KEY", NEW_KEY)
        monkeypatch.setenv("JWT_SECRET_KEY_PREVIOUS", OLD_KEY)
        reset_keyring()
        forged = jwt.encode({"sub": "attacker@evil.io"}, "not-one-of-our-keys", algorithm=ALGORITHM)
        with pytest.raises(JWTError):
            decode_token(forged)
        with pytest.raises(JWTError):
            decode_token("not-a-jwt-at-all")

    def test_active_key_is_never_also_listed_as_retired(self, monkeypatch, no_refresh_delay):
        monkeypatch.setenv("JWT_SECRET_KEY", NEW_KEY)
        monkeypatch.setenv("JWT_SECRET_KEY_PREVIOUS", f"{NEW_KEY},{OLD_KEY}")
        reset_keyring()
        ring = get_keyring()
        assert [k.secret for k in ring.retired] == [OLD_KEY]


class TestFileBackedSecrets:
    def test_secret_is_read_from_the_mounted_file(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "jwt-secret"
        secret_file.write_text(f"  {NEW_KEY}\n")
        monkeypatch.setenv("JWT_SECRET_KEY_FILE", str(secret_file))
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        assert read_secret("JWT_SECRET_KEY") == NEW_KEY

    def test_missing_file_falls_back_to_the_env_var(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JWT_SECRET_KEY_FILE", str(tmp_path / "absent"))
        monkeypatch.setenv("JWT_SECRET_KEY", OLD_KEY)
        assert read_secret("JWT_SECRET_KEY") == OLD_KEY

    def test_rotating_the_file_rotates_the_live_keyring(self, monkeypatch, tmp_path, no_refresh_delay):
        """An external rotator updates the mount; the process picks it up."""
        secret_file = tmp_path / "jwt-secret"
        secret_file.write_text(OLD_KEY)
        monkeypatch.setenv("JWT_SECRET_KEY_FILE", str(secret_file))
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("JWT_SECRET_KEY_PREVIOUS", raising=False)
        reset_keyring()
        assert get_keyring().active.secret == OLD_KEY

        previous_file = tmp_path / "jwt-secret-previous"
        previous_file.write_text(OLD_KEY)
        secret_file.write_text(NEW_KEY)
        monkeypatch.setenv("JWT_SECRET_KEY_PREVIOUS_FILE", str(previous_file))

        ring = get_keyring()
        assert ring.active.secret == NEW_KEY
        assert [k.secret for k in ring.retired] == [OLD_KEY]

    def test_settings_read_any_secret_from_a_file(self, monkeypatch, tmp_path):
        scim_file = tmp_path / "scim-token"
        scim_file.write_text("scim-token-from-vault\n")
        monkeypatch.setenv("SCIM_BEARER_TOKEN_FILE", str(scim_file))
        monkeypatch.delenv("SCIM_BEARER_TOKEN", raising=False)
        assert InfraSettings().scim_bearer_token == "scim-token-from-vault"

    def test_explicit_env_var_beats_the_file(self, monkeypatch, tmp_path):
        scim_file = tmp_path / "scim-token"
        scim_file.write_text("from-file")
        monkeypatch.setenv("SCIM_BEARER_TOKEN_FILE", str(scim_file))
        monkeypatch.setenv("SCIM_BEARER_TOKEN", "from-env")
        assert InfraSettings().scim_bearer_token == "from-env"


class TestProductionValidation:
    def _settings(self, **kwargs) -> InfraSettings:
        base = {
            "environment": "production",
            "jwt_secret_key": NEW_KEY,
            "admin_password": "a-real-admin-password",
            "database_url": "postgresql://user:pass@db:5432/semanticos",
            "cors_allowed_origins": "https://app.example.com",
        }
        return InfraSettings(**{**base, **kwargs})

    def test_clean_rotation_passes(self):
        problems = validate_for_production(self._settings(jwt_secret_key_previous=OLD_KEY))
        assert problems == []

    def test_rotation_that_did_not_take_effect_is_caught(self):
        problems = validate_for_production(
            self._settings(jwt_secret_key_previous=f"{NEW_KEY},{OLD_KEY}")
        )
        assert any("rotation never took effect" in p for p in problems)

    def test_retired_known_insecure_key_is_caught(self):
        problems = validate_for_production(
            self._settings(jwt_secret_key_previous=KNOWN_INSECURE_JWT_SECRET)
        )
        assert any("still accepted" in p for p in problems)
