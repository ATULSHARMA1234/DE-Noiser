"""One consultant, two customers, one deployment.

`users.email` was globally unique, so the first organisation to employ someone
owned their address for the whole deployment: the second organisation's admin
got "User with this email already exists" for a person who had no account with
them, and no way to create one. SCIM hit the same wall, and so did onboarding a
customer whose first admin already worked somewhere else.

These tests are the behaviour that constraint made impossible. The interesting
half is not that two rows can exist — it is that a token, a sign-in and a quota
bucket each resolve to exactly one of them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from denoiser.api.auth import get_password_hash, issue_token_pair, user_for_claims
from denoiser.api.main import app
from denoiser.storage.db import SessionLocal, Tenant, User, init_db

SHARED_EMAIL = "consultant@shared-email.test"
PASSWORD_ACME = "acme-password-9174"
PASSWORD_GLOBEX = "globex-password-3382"


@pytest.fixture
def two_orgs():
    """Two organisations, each employing the same consultant."""
    init_db()
    db = SessionLocal()
    tenants, users = [], []
    try:
        for name, password in (
            ("shared-email-acme", PASSWORD_ACME),
            ("shared-email-globex", PASSWORD_GLOBEX),
        ):
            tenant = Tenant(name=name, tier="enterprise")
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            tenants.append(tenant)

            user = User(
                email=SHARED_EMAIL,
                hashed_password=get_password_hash(password),
                role="ADMIN",
                tenant_id=tenant.id,
                is_active=True,
                department="Consulting",
                environment_access=["*"],
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            users.append(user)

        yield {
            "acme": {"tenant": tenants[0].id, "user": users[0].id},
            "globex": {"tenant": tenants[1].id, "user": users[1].id},
        }
    finally:
        for user in users:
            db.query(User).filter(User.id == user.id).delete()
        for tenant in tenants:
            db.query(Tenant).filter(Tenant.id == tenant.id).delete()
        db.commit()
        db.close()


def test_two_organisations_can_each_employ_the_same_address(two_orgs):
    """The constraint change itself: two rows, two organisations, one address."""
    db = SessionLocal()
    try:
        rows = db.query(User).filter(User.email == SHARED_EMAIL).all()
        assert len(rows) == 2
        assert {r.tenant_id for r in rows} == {
            two_orgs["acme"]["tenant"],
            two_orgs["globex"]["tenant"],
        }
    finally:
        db.close()


def test_the_same_address_cannot_be_used_twice_inside_one_organisation(two_orgs):
    """Scoping uniqueness is not removing it."""
    from sqlalchemy.exc import IntegrityError

    db = SessionLocal()
    try:
        db.add(User(
            email=SHARED_EMAIL,
            hashed_password=get_password_hash("irrelevant"),
            role="VIEWER",
            tenant_id=two_orgs["acme"]["tenant"],
        ))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_a_token_resolves_to_the_organisation_it_was_issued_for(two_orgs):
    """The `tid` claim is what stops one address naming two accounts."""
    db = SessionLocal()
    try:
        for org in ("acme", "globex"):
            claims = {"sub": SHARED_EMAIL, "tid": two_orgs[org]["tenant"]}
            resolved = user_for_claims(db, claims)
            assert resolved is not None
            assert resolved.id == two_orgs[org]["user"]
    finally:
        db.close()


def test_a_token_without_a_tenant_claim_is_refused_when_it_is_ambiguous(two_orgs):
    """A token from before `tid` existed must not pick a row at random.

    Picking would sign the holder in to whichever customer the database returned
    first — the failure this whole change exists to prevent.
    """
    db = SessionLocal()
    try:
        assert user_for_claims(db, {"sub": SHARED_EMAIL}) is None
    finally:
        db.close()


def test_a_token_without_a_tenant_claim_still_works_when_it_is_not_ambiguous():
    """Sessions issued before the change keep working for everybody else."""
    init_db()
    db = SessionLocal()
    email = "solo@shared-email.test"
    try:
        tenant = Tenant(name="shared-email-solo")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        db.add(User(
            email=email,
            hashed_password=get_password_hash("whatever-4471"),
            role="VIEWER",
            tenant_id=tenant.id,
        ))
        db.commit()

        resolved = user_for_claims(db, {"sub": email})
        assert resolved is not None and resolved.email == email
    finally:
        db.query(User).filter(User.email == email).delete()
        db.query(Tenant).filter(Tenant.name == "shared-email-solo").delete()
        db.commit()
        db.close()


def test_issued_tokens_carry_the_tenant(two_orgs):
    from denoiser.api.auth import decode_token

    tokens = issue_token_pair(SHARED_EMAIL, two_orgs["globex"]["tenant"])
    for kind in ("access_token", "refresh_token"):
        claims = decode_token(tokens[kind])
        assert claims["sub"] == SHARED_EMAIL
        assert claims["tid"] == two_orgs["globex"]["tenant"]


def test_login_picks_the_account_whose_password_was_given(two_orgs):
    """Nobody has to name their organisation while the password distinguishes."""
    with TestClient(app) as client:
        for org, password in (("acme", PASSWORD_ACME), ("globex", PASSWORD_GLOBEX)):
            response = client.post(
                "/auth/login", json={"email": SHARED_EMAIL, "password": password}
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["user"]["tenant_id"] == two_orgs[org]["tenant"]


def test_login_with_an_explicit_organisation_selects_that_account(two_orgs):
    with TestClient(app) as client:
        response = client.post("/auth/login", json={
            "email": SHARED_EMAIL,
            "password": PASSWORD_ACME,
            "tenant": "SHARED-EMAIL-ACME",  # matched case-insensitively
        })
        assert response.status_code == 200, response.text
        assert response.json()["user"]["tenant_id"] == two_orgs["acme"]["tenant"]


def test_login_with_the_wrong_organisation_is_refused(two_orgs):
    """The Acme password must not open the Globex account."""
    with TestClient(app) as client:
        response = client.post("/auth/login", json={
            "email": SHARED_EMAIL,
            "password": PASSWORD_ACME,
            "tenant": "shared-email-globex",
        })
        assert response.status_code == 401


def test_login_asks_which_organisation_only_when_it_genuinely_cannot_tell(two_orgs):
    """Same address, same password, two organisations.

    The 409 names them, which is only reachable by someone already holding a
    working credential for both.
    """
    db = SessionLocal()
    try:
        globex_user = db.query(User).filter(User.id == two_orgs["globex"]["user"]).first()
        globex_user.hashed_password = get_password_hash(PASSWORD_ACME)
        db.commit()
    finally:
        db.close()

    with TestClient(app) as client:
        response = client.post(
            "/auth/login", json={"email": SHARED_EMAIL, "password": PASSWORD_ACME}
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "shared-email-acme" in detail and "shared-email-globex" in detail

        # And the ambiguity is resolvable by saying which one.
        resolved = client.post("/auth/login", json={
            "email": SHARED_EMAIL,
            "password": PASSWORD_ACME,
            "tenant": "shared-email-globex",
        })
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["user"]["tenant_id"] == two_orgs["globex"]["tenant"]


def test_an_authenticated_request_acts_as_the_right_account(two_orgs):
    """End to end: the token from one organisation sees that organisation."""
    with TestClient(app) as client:
        token = client.post(
            "/auth/login", json={"email": SHARED_EMAIL, "password": PASSWORD_GLOBEX}
        ).json()["access_token"]

        response = client.get("/users", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        listed = response.json()
        assert [u["id"] for u in listed] == [two_orgs["globex"]["user"]]
