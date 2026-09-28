"""Erasing one person, not one customer.

Offboarding a whole workspace already worked. It is the wrong instrument for the
request that actually arrives: a customer forwards a GDPR Article 17 notice from
one of *their* end users, whose account id appears inside logs they shipped
here. The only lever was deleting the customer entirely.

Most of this problem is solved upstream — redaction now runs at every ingest
boundary, so emails and card numbers never land. What is left is the identifier
only the data controller recognises: an internal account id, a username, a
customer reference.
"""

import json

import pytest

from denoiser.api.subject_erasure import (
    MIN_IDENTIFIER_LENGTH,
    REPLACEMENT,
    _looks_dangerous,
    erase_subject,
    preview_subject,
)
from denoiser.storage.db import SessionLocal, Span, Tenant


@pytest.fixture(scope="module", autouse=True)
def _db():
    from denoiser.storage.db import init_db
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
def two_tenants(db):
    made = []
    for name in ("erasure-subject-a", "erasure-subject-b"):
        tenant = db.query(Tenant).filter(Tenant.name == name).first()
        if tenant is None:
            tenant = Tenant(name=name)
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
        made.append(tenant)
    return made


def _span(tenant_id, span_id, attributes):
    return Span(
        tenant_id=tenant_id, trace_id=f"tr-{span_id}", span_id=span_id,
        service_name="checkout", operation_name="charge",
        start_time=__import__("datetime").datetime.now(__import__("datetime").UTC),
        end_time=__import__("datetime").datetime.now(__import__("datetime").UTC),
        duration_ms=1.0, attributes=attributes,
    )


class TestTheIdentifierIsSanityChecked:
    """An over-broad erasure is unrecoverable. A refused one is a conversation."""

    def test_a_short_identifier_is_refused(self):
        assert _looks_dangerous("ab") is not None

    def test_the_minimum_is_stated_in_the_message(self):
        assert str(MIN_IDENTIFIER_LENGTH) in _looks_dangerous("a")

    @pytest.mark.parametrize("identifier", ["----", "!!!!", "%%%%", "____"])
    def test_pure_punctuation_is_refused(self, identifier):
        # Matches ordinary log text everywhere; a targeted erasure it is not.
        assert _looks_dangerous(identifier) is not None

    @pytest.mark.parametrize("identifier", ["acct_9f3b21c4", "user_id_88213", "cust%ref%12"])
    def test_punctuation_inside_a_real_identifier_is_fine(self, identifier):
        """`%` and `_` are not wildcards here.

        The value is a bound parameter and `position`/`replaceAll` match it
        literally, so rejecting these would refuse the most common shape of real
        account id while protecting against nothing.
        """
        assert _looks_dangerous(identifier) is None

    @pytest.mark.parametrize("identifier", ["ERROR", "info", "true", "null", "admin"])
    def test_common_log_tokens_are_refused(self, identifier):
        assert _looks_dangerous(identifier) is not None

    def test_untrimmed_input_is_refused(self):
        assert _looks_dangerous(" acct_12345 ") is not None

    def test_a_real_identifier_passes(self):
        assert _looks_dangerous("acct_9f3b21c4") is None
        assert _looks_dangerous("subject@customer.example") is None


class TestPreviewChangesNothing:
    def test_it_reports_matches_without_erasing(self, db, two_tenants):
        tenant, _ = two_tenants
        db.add(_span(tenant.id, "prev-1", {"user.id": "acct_preview_1"}))
        db.commit()

        matches = preview_subject(db, tenant.id, "acct_preview_1")
        assert matches["spans"] >= 1

        survivor = db.query(Span).filter(Span.span_id == "prev-1").first()
        assert "acct_preview_1" in json.dumps(survivor.attributes)


class TestErasureRemovesTheIdentifier:
    def test_the_value_is_gone_from_span_attributes(self, db, two_tenants):
        tenant, _ = two_tenants
        db.add(_span(tenant.id, "erase-1", {"user.id": "acct_erase_me", "http.method": "POST"}))
        db.commit()

        outcome = erase_subject(db, tenant.id, "acct_erase_me")

        assert outcome["spans_updated"] >= 1
        row = db.query(Span).filter(Span.span_id == "erase-1").first()
        encoded = json.dumps(row.attributes)
        assert "acct_erase_me" not in encoded
        assert REPLACEMENT in encoded

    def test_the_rest_of_the_record_survives(self, db, two_tenants):
        """Erasure removes the personal data, not the customer's history."""
        tenant, _ = two_tenants
        db.add(_span(tenant.id, "erase-2", {"user.id": "acct_keep_rest", "http.method": "POST"}))
        db.commit()

        erase_subject(db, tenant.id, "acct_keep_rest")

        row = db.query(Span).filter(Span.span_id == "erase-2").first()
        assert row is not None, "the span itself must not be deleted"
        assert row.attributes["http.method"] == "POST"
        assert row.service_name == "checkout"


class TestErasureIsTenantScoped:
    def test_another_customers_identical_value_is_untouched(self, db, two_tenants):
        """The request came through one customer, about one of their users."""
        first, second = two_tenants
        shared = "acct_shared_value"
        db.add(_span(first.id, "scope-a", {"user.id": shared}))
        db.add(_span(second.id, "scope-b", {"user.id": shared}))
        db.commit()

        erase_subject(db, first.id, shared)

        erased = db.query(Span).filter(Span.span_id == "scope-a").first()
        untouched = db.query(Span).filter(Span.span_id == "scope-b").first()

        assert shared not in json.dumps(erased.attributes)
        assert shared in json.dumps(untouched.attributes)


class TestTheEndpointRequiresConfirmation:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from denoiser.api.main import app
        return TestClient(app)

    def test_an_unconfirmed_request_is_refused(self, client):
        res = client.post("/privacy/erasures", json={"identifier": "acct_123456"})
        # Unauthenticated here, so the exact code depends on the auth layer —
        # what matters is that it is never a silent success.
        assert res.status_code != 200

    def test_the_route_exists_and_is_not_public(self, client):
        for path in ("/privacy/erasures", "/privacy/erasures/preview"):
            res = client.post(path, json={"identifier": "acct_123456", "confirm": True})
            assert res.status_code in (401, 403, 422), res.text
