"""Rotation for the credentials that are not the JWT signing key.

The signing key already rotated through an overlap window. The tenant API key —
the credential customers paste into log shippers — had no rotation path at all,
and the static ingest and SCIM tokens were single values, so changing either
broke every agent at the same instant. A secret nobody can rotate safely is a
secret nobody rotates.
"""

import datetime

import pytest

from denoiser.api.credentials import (
    describe_static_rotation,
    generate_api_key,
    matches_static_secret,
    revoke_previous_api_key,
    rotate_tenant_api_key,
    secrets_match,
    tenant_for_api_key,
)
from denoiser.storage.db import SessionLocal, Tenant, init_db
from denoiser.utils.time import utcnow


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def tenant(db):
    row = Tenant(name=f"rotation-test-{utcnow().timestamp()}", api_key=generate_api_key(), tier="pro")
    db.add(row)
    db.commit()
    db.refresh(row)
    yield row
    db.delete(row)
    db.commit()


class TestKeyGeneration:
    def test_keys_are_prefixed_and_unique(self):
        first, second = generate_api_key(), generate_api_key()
        assert first.startswith("sk_") and second.startswith("sk_")
        assert first != second

    def test_keys_are_long_enough_to_resist_guessing(self):
        assert len(generate_api_key()) >= 40

    def test_comparison_tolerates_missing_values(self):
        assert secrets_match("abc", "abc") is True
        assert secrets_match("abc", "abd") is False
        assert secrets_match(None, "abc") is False
        assert secrets_match("abc", None) is False


class TestTenantKeyRotation:
    def test_rotation_issues_a_new_key(self, db, tenant):
        original = tenant.api_key
        new_key = rotate_tenant_api_key(db, tenant)
        assert new_key != original
        assert tenant.api_key == new_key
        assert tenant.api_key_rotated_at is not None

    def test_superseded_key_still_authenticates_during_overlap(self, db, tenant):
        original = tenant.api_key
        rotate_tenant_api_key(db, tenant, overlap_hours=24)

        assert tenant_for_api_key(db, tenant.api_key).id == tenant.id
        assert tenant_for_api_key(db, original).id == tenant.id, (
            "a shipper still on the old key must keep working through the window"
        )

    def test_superseded_key_stops_working_once_the_window_closes(self, db, tenant):
        original = tenant.api_key
        rotate_tenant_api_key(db, tenant, overlap_hours=1)

        later = utcnow() + datetime.timedelta(hours=2)
        assert tenant_for_api_key(db, original, now=later) is None
        assert tenant_for_api_key(db, tenant.api_key, now=later).id == tenant.id

    def test_zero_overlap_revokes_immediately(self, db, tenant):
        """The right behaviour for a leaked key."""
        original = tenant.api_key
        rotate_tenant_api_key(db, tenant, overlap_hours=0)

        assert tenant.api_key_previous is None
        assert tenant_for_api_key(db, original) is None

    def test_revoking_the_previous_key_ends_the_overlap_early(self, db, tenant):
        original = tenant.api_key
        rotate_tenant_api_key(db, tenant, overlap_hours=24)
        assert tenant_for_api_key(db, original) is not None

        assert revoke_previous_api_key(db, tenant) is True
        assert tenant_for_api_key(db, original) is None
        assert tenant_for_api_key(db, tenant.api_key).id == tenant.id

    def test_revoking_when_there_is_no_overlap_is_a_no_op(self, db, tenant):
        assert revoke_previous_api_key(db, tenant) is False

    def test_a_second_rotation_drops_the_oldest_key(self, db, tenant):
        """Only one superseded key is ever accepted, never a growing chain."""
        first = tenant.api_key
        rotate_tenant_api_key(db, tenant, overlap_hours=24)
        second = tenant.api_key
        rotate_tenant_api_key(db, tenant, overlap_hours=24)

        assert tenant_for_api_key(db, first) is None
        assert tenant_for_api_key(db, second).id == tenant.id

    def test_unknown_and_empty_keys_resolve_to_nothing(self, db, tenant):
        assert tenant_for_api_key(db, "sk_not_a_real_key") is None
        assert tenant_for_api_key(db, None) is None
        assert tenant_for_api_key(db, "") is None


class TestStaticSecretRotation:
    def test_current_value_matches(self, monkeypatch):
        monkeypatch.setenv("INGEST_API_KEY", "current-token")
        assert matches_static_secret("current-token", "INGEST_API_KEY") is True
        assert matches_static_secret("other-token", "INGEST_API_KEY") is False

    def test_superseded_value_is_accepted_during_rotation(self, monkeypatch):
        monkeypatch.setenv("INGEST_API_KEY", "new-token")
        monkeypatch.setenv("INGEST_API_KEY_PREVIOUS", "old-token")
        assert matches_static_secret("new-token", "INGEST_API_KEY") is True
        assert matches_static_secret("old-token", "INGEST_API_KEY") is True

    def test_multiple_retired_values_are_comma_separated(self, monkeypatch):
        monkeypatch.setenv("SCIM_BEARER_TOKEN", "t3")
        monkeypatch.setenv("SCIM_BEARER_TOKEN_PREVIOUS", "t2, t1")
        for token in ("t3", "t2", "t1"):
            assert matches_static_secret(token, "SCIM_BEARER_TOKEN") is True
        assert matches_static_secret("t0", "SCIM_BEARER_TOKEN") is False

    def test_dropping_the_previous_value_completes_the_rotation(self, monkeypatch):
        monkeypatch.setenv("INGEST_API_KEY", "new-token")
        monkeypatch.delenv("INGEST_API_KEY_PREVIOUS", raising=False)
        assert matches_static_secret("old-token", "INGEST_API_KEY") is False

    def test_nothing_matches_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("INGEST_API_KEY", raising=False)
        monkeypatch.delenv("INGEST_API_KEY_PREVIOUS", raising=False)
        assert matches_static_secret("anything", "INGEST_API_KEY") is False

    def test_status_reports_overlap_without_exposing_the_secret(self, monkeypatch):
        monkeypatch.setenv("INGEST_API_KEY", "sensitive-value")
        monkeypatch.setenv("INGEST_API_KEY_PREVIOUS", "older-sensitive-value")

        status = describe_static_rotation("INGEST_API_KEY")
        assert status == {
            "configured": True,
            "accepted_values": 2,
            "overlap_active": True,
            "source": "env",
        }
        assert "sensitive" not in str(status)
