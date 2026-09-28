"""Two companies on one deployment: separate from each other, joined within.

Every test here is a reproduction of something that was true of the audited
build. SSO and SCIM both attributed federated identities to whichever tenant
sorted first, the vector store held every customer's log templates in one
untagged table, cold archives put two companies' rows in the same gzip, and
there was no way to remove a customer at all.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from denoiser.api.main import app
from denoiser.storage.db import SessionLocal, Tenant, User

PLATFORM_TOKEN = "platform-operator-token-for-tests"
ACME_DOMAIN = "acme-isolation.test"
GLOBEX_DOMAIN = "globex-isolation.test"


@pytest.fixture(scope="module")
def _schema():
    from denoiser.storage.db import init_db

    init_db()


@pytest.fixture
def orgs(_schema):
    """Two customers, each owning their own email domain.

    Domains are torn down again because `domain_routing_configured` is a
    deployment-wide signal: leaving one behind would switch every other test
    module's single-customer deployment into shared-hosting mode.
    """
    db = SessionLocal()
    created = []
    try:
        for name, domain in (("acme-isolation", ACME_DOMAIN), ("globex-isolation", GLOBEX_DOMAIN)):
            tenant = db.query(Tenant).filter(Tenant.name == name).first()
            if tenant is None:
                tenant = Tenant(name=name, tier="enterprise")
                db.add(tenant)
                db.commit()
                db.refresh(tenant)
            tenant.sso_domains = [domain]
            created.append(tenant)
        db.commit()
        yield {"acme": created[0].id, "globex": created[1].id}
    finally:
        for tenant in created:
            tenant.sso_domains = []
        db.commit()
        db.close()


@pytest.fixture
def client(_schema):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def operator():
    """A configured platform operator token, removed again afterwards."""
    os.environ["SEMANTICOS_PLATFORM_TOKEN"] = PLATFORM_TOKEN
    yield {"Authorization": f"Bearer {PLATFORM_TOKEN}"}
    os.environ.pop("SEMANTICOS_PLATFORM_TOKEN", None)


# ── Domain routing ───────────────────────────────────────────────────────────

class TestIdentityRouting:
    """Every federated identity used to land in the lowest-id tenant."""

    def test_email_domain_selects_the_owning_organisation(self, orgs):
        from denoiser.api.tenancy import resolve_identity_tenant

        db = SessionLocal()
        try:
            assert resolve_identity_tenant(db, f"ceo@{ACME_DOMAIN}") == orgs["acme"]
            assert resolve_identity_tenant(db, f"ceo@{GLOBEX_DOMAIN}") == orgs["globex"]
        finally:
            db.close()

    def test_unregistered_domain_is_refused_not_guessed(self, orgs):
        """The original bug: an unknown domain silently joined the first tenant."""
        from denoiser.api.tenancy import TenantRoutingError, resolve_identity_tenant

        db = SessionLocal()
        try:
            with pytest.raises(TenantRoutingError):
                resolve_identity_tenant(db, "stranger@nobody-registered-this.test")
        finally:
            db.close()

    def test_single_customer_deployment_still_falls_back(self, _schema):
        """With no domains registered anywhere, there is only one place to go."""
        from denoiser.api.tenancy import domain_routing_configured, resolve_identity_tenant

        db = SessionLocal()
        try:
            assert not domain_routing_configured(db)
            first = db.query(Tenant).order_by(Tenant.id).first()
            assert resolve_identity_tenant(db, "anyone@whatever.test") == (first.id if first else None)
        finally:
            db.close()

    def test_a_domain_cannot_be_claimed_by_two_organisations(self, orgs):
        from denoiser.api.tenancy import conflicting_domains

        db = SessionLocal()
        try:
            assert conflicting_domains(db, [ACME_DOMAIN]) == [ACME_DOMAIN]
            # Not a conflict for the tenant that already owns it.
            assert conflicting_domains(db, [ACME_DOMAIN], exclude_tenant_id=orgs["acme"]) == []
        finally:
            db.close()

    def test_domains_are_normalised(self):
        from denoiser.api.tenancy import normalise_domains

        assert normalise_domains(["@ACME.com", "acme.com ", "", "Globex.io."]) == [
            "acme.com", "globex.io",
        ]


class TestSsoProvisioningRespectsTheBoundary:
    def test_a_new_sso_user_joins_the_company_that_owns_their_domain(self, orgs):
        from denoiser.api.sso import _provision_sso_user

        db = SessionLocal()
        try:
            email = f"sso-newhire@{GLOBEX_DOMAIN}"
            db.query(User).filter(User.email == email).delete()
            db.commit()

            user = _provision_sso_user(db, {"email": email, "role": "ANALYST"})
            assert user.tenant_id == orgs["globex"], (
                "an SSO user was seated in the wrong company"
            )
        finally:
            db.query(User).filter(User.email == f"sso-newhire@{GLOBEX_DOMAIN}").delete()
            db.commit()
            db.close()

    def test_an_unrecognised_domain_cannot_sign_in(self, orgs):
        from fastapi import HTTPException

        from denoiser.api.sso import _provision_sso_user

        db = SessionLocal()
        try:
            with pytest.raises(HTTPException) as exc:
                _provision_sso_user(db, {"email": "intruder@unknown-company.test"})
            assert exc.value.status_code == 403
        finally:
            db.close()


# ── SCIM ─────────────────────────────────────────────────────────────────────

class TestScimIsScopedToOneCompany:
    """One shared SCIM token let any IdP manage every customer's staff."""

    @pytest.fixture
    def provisioned(self, orgs):
        """A SCIM token per company, and one existing employee inside Globex."""
        from denoiser.api.auth import get_password_hash
        from denoiser.api.tenancy import rotate_scim_token

        db = SessionLocal()
        tokens = {}
        victim_email = f"globex-employee@{GLOBEX_DOMAIN}"
        try:
            for key in ("acme", "globex"):
                tenant = db.query(Tenant).filter(Tenant.id == orgs[key]).first()
                tokens[key] = rotate_scim_token(db, tenant)

            db.query(User).filter(User.email == victim_email).delete()
            db.commit()
            victim = User(
                email=victim_email,
                hashed_password=get_password_hash("irrelevant"),
                role="ANALYST",
                tenant_id=orgs["globex"],
            )
            db.add(victim)
            db.commit()
            db.refresh(victim)
            yield tokens, victim.id, victim_email
        finally:
            db.query(User).filter(User.email == victim_email).delete()
            for key in ("acme", "globex"):
                tenant = db.query(Tenant).filter(Tenant.id == orgs[key]).first()
                if tenant:
                    tenant.scim_token = None
            db.commit()
            db.close()

    def _h(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_a_companys_idp_only_lists_its_own_staff(self, client, provisioned):
        tokens, _, victim_email = provisioned
        res = client.get("/scim/v2/Users", headers=self._h(tokens["acme"]))
        assert res.status_code == 200
        assert victim_email not in res.text

        res = client.get("/scim/v2/Users", headers=self._h(tokens["globex"]))
        assert victim_email in res.text

    def test_a_companys_idp_cannot_read_anothers_employee(self, client, provisioned):
        tokens, victim_id, _ = provisioned
        res = client.get(f"/scim/v2/Users/{victim_id}", headers=self._h(tokens["acme"]))
        assert res.status_code == 404, "another company's employee was readable over SCIM"

    def test_a_companys_idp_cannot_deprovision_anothers_employee(self, client, provisioned):
        """The sharpest edge of the original bug: a denial-of-service on a rival."""
        tokens, victim_id, victim_email = provisioned
        res = client.delete(f"/scim/v2/Users/{victim_id}", headers=self._h(tokens["acme"]))
        assert res.status_code == 404

        db = SessionLocal()
        try:
            assert db.query(User).filter(User.email == victim_email).first().is_active
        finally:
            db.close()

    def test_a_companys_idp_cannot_patch_anothers_employee(self, client, provisioned):
        tokens, victim_id, _ = provisioned
        res = client.patch(
            f"/scim/v2/Users/{victim_id}",
            headers=self._h(tokens["acme"]),
            json={"Operations": [{"op": "replace", "path": "role", "value": "ADMIN"}]},
        )
        assert res.status_code == 404

    def test_provisioned_users_join_the_authenticating_company(self, client, provisioned):
        tokens, _, _ = provisioned
        email = f"scim-newhire@{ACME_DOMAIN}"
        db = SessionLocal()
        db.query(User).filter(User.email == email).delete()
        db.commit()
        db.close()

        res = client.post(
            "/scim/v2/Users",
            headers=self._h(tokens["acme"]),
            json={"userName": email, "active": True},
        )
        assert res.status_code == 201, res.text

        db = SessionLocal()
        try:
            from denoiser.api.tenancy import tenant_claiming

            user = db.query(User).filter(User.email == email).first()
            assert user.tenant_id == tenant_claiming(db, ACME_DOMAIN).id
        finally:
            db.query(User).filter(User.email == email).delete()
            db.commit()
            db.close()

    def test_the_deployment_wide_token_is_refused_on_a_shared_install(self, client, orgs, monkeypatch):
        """It authenticates, but it names no organisation — so it cannot be used."""
        monkeypatch.setenv("SCIM_BEARER_TOKEN", "deployment-wide-token")
        from denoiser.settings import reload_settings

        reload_settings()
        try:
            res = client.get("/scim/v2/Users", headers=self._h("deployment-wide-token"))
            assert res.status_code == 403
            assert "per-organisation" in res.text
        finally:
            monkeypatch.delenv("SCIM_BEARER_TOKEN", raising=False)
            reload_settings()


# ── Platform operations ──────────────────────────────────────────────────────

class TestPlatformAdministration:
    def test_disabled_until_an_operator_token_is_configured(self, client):
        os.environ.pop("SEMANTICOS_PLATFORM_TOKEN", None)
        assert client.get("/platform/tenants").status_code == 403

    def test_a_customer_admin_cannot_reach_it(self, client, operator):
        """Onboarding and offboarding is the vendor's job, not a customer's."""
        res = client.get("/platform/tenants", headers={"Authorization": "Bearer not-the-operator"})
        assert res.status_code == 401

    def test_onboarding_returns_credentials_once(self, client, operator):
        res = client.post(
            "/platform/tenants",
            headers=operator,
            json={"name": "onboarded-co", "domains": ["Onboarded-Co.test"], "tier": "pro"},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["api_key"] and body["scim_token"]
        assert body["domains"] == ["onboarded-co.test"]

        # Not retrievable a second time.
        listed = client.get("/platform/tenants", headers=operator).json()["tenants"]
        mine = next(t for t in listed if t["name"] == "onboarded-co")
        assert "api_key" not in mine and "scim_token" not in mine
        assert mine["scim_token_configured"] is True

        _drop_tenant("onboarded-co")

    def test_onboarding_seeds_an_admin_who_can_actually_sign_in(self, client, operator):
        """Without this the organisation exists and nobody can get into it.

        There is no self-registration, and the seeded admin belongs to the
        default tenant, so a new customer had no way in at all.
        """
        res = client.post(
            "/platform/tenants",
            headers=operator,
            json={"name": "bootstrap-co", "admin_email": "boss@bootstrap-co.test"},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["admin_password"]

        try:
            login = client.post("/auth/login", json={
                "email": "boss@bootstrap-co.test",
                "password": body["admin_password"],
            })
            assert login.status_code == 200, login.text
            assert login.json()["user"]["role"] == "ADMIN"
            assert login.json()["user"]["tenant_id"] == body["id"]

            # And that admin can invite their own colleagues.
            client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
            invited = client.post("/users", json={
                "email": "analyst@bootstrap-co.test",
                "password": "ColleaguePass!2026",
                "role": "ANALYST",
            })
            assert invited.status_code in (200, 201), invited.text
            assert invited.json()["tenant_id"] == body["id"]
        finally:
            client.headers.pop("Authorization", None)
            db = SessionLocal()
            db.query(User).filter(User.email.in_([
                "boss@bootstrap-co.test", "analyst@bootstrap-co.test",
            ])).delete(synchronize_session=False)
            db.commit()
            db.close()
            _drop_tenant("bootstrap-co")

    def test_a_first_admin_on_another_companys_domain_is_refused(self, client, operator, orgs):
        """Their next SSO login would route them into the other organisation."""
        res = client.post(
            "/platform/tenants",
            headers=operator,
            json={"name": "confused-co", "admin_email": f"boss@{ACME_DOMAIN}"},
        )
        assert res.status_code == 409
        assert "acme-isolation" in res.text
        # And nothing was left half-created.
        db = SessionLocal()
        try:
            assert db.query(Tenant).filter(Tenant.name == "confused-co").first() is None
        finally:
            db.close()

    def test_two_customers_cannot_claim_the_same_domain(self, client, operator, orgs):
        res = client.post(
            "/platform/tenants",
            headers=operator,
            json={"name": "domain-squatter", "domains": [ACME_DOMAIN]},
        )
        assert res.status_code == 409
        assert ACME_DOMAIN in res.text

    def test_deletion_requires_the_name_to_be_typed_back(self, client, operator):
        created = client.post(
            "/platform/tenants", headers=operator, json={"name": "typo-guard-co"}
        ).json()
        try:
            res = client.request(
                "DELETE",
                f"/platform/tenants/{created['id']}",
                headers=operator,
                json={"confirm_name": "some-other-co"},
            )
            assert res.status_code == 400
        finally:
            _drop_tenant("typo-guard-co")

    def test_offboarding_removes_the_customers_data(self, client, operator):
        """There was previously no way to remove a customer at all."""
        from denoiser.api.auth import get_password_hash
        from denoiser.storage.db import Dashboard, LogIssue

        created = client.post(
            "/platform/tenants", headers=operator, json={"name": "departing-co"}
        ).json()
        tenant_id = created["id"]

        db = SessionLocal()
        db.add(User(
            email="staff@departing-co.test",
            hashed_password=get_password_hash("irrelevant"),
            role="ADMIN",
            tenant_id=tenant_id,
        ))
        db.add(Dashboard(name="Their board", tenant_id=tenant_id, layout={}))
        db.add(LogIssue(title="Their issue", tenant_id=tenant_id, fingerprint="departing-fp"))
        db.commit()
        db.close()

        res = client.request(
            "DELETE",
            f"/platform/tenants/{tenant_id}",
            headers=operator,
            json={"confirm_name": "departing-co"},
        )
        assert res.status_code == 200, res.text
        report = res.json()
        assert report["deleted"]["users"] == 1
        assert report["deleted"]["dashboards"] == 1
        assert report["deleted"]["log_issues"] == 1

        db = SessionLocal()
        try:
            assert db.query(Tenant).filter(Tenant.id == tenant_id).first() is None
            assert db.query(User).filter(User.tenant_id == tenant_id).count() == 0
            assert db.query(Dashboard).filter(Dashboard.tenant_id == tenant_id).count() == 0
            assert db.query(LogIssue).filter(LogIssue.tenant_id == tenant_id).count() == 0
        finally:
            db.close()

    def test_every_table_is_accounted_for_by_offboarding(self):
        """A table added later must not be silently left behind on a purge.

        Each model has to be classified as owned by a customer, owned through a
        parent, or deliberately deployment-wide — there is no fourth option that
        quietly means "keep this customer's rows forever".
        """
        from denoiser.api.tenancy import (
            CHILD_MODELS,
            GLOBAL_MODELS,
            TENANT_SCOPED_MODELS,
        )
        from denoiser.storage import db as models

        classified = set(TENANT_SCOPED_MODELS) | set(GLOBAL_MODELS) | {c[0] for c in CHILD_MODELS}
        mapped = {cls.__name__ for cls in models.Base.__subclasses__()}
        assert mapped - classified == set(), (
            f"tables unclassified by offboarding: {sorted(mapped - classified)}"
        )

        # And everything named as tenant-owned really is.
        for name in TENANT_SCOPED_MODELS:
            assert hasattr(getattr(models, name), "tenant_id"), f"{name} has no tenant_id"


def _drop_tenant(name: str) -> None:
    db = SessionLocal()
    try:
        db.query(Tenant).filter(Tenant.name == name).delete()
        db.commit()
    finally:
        db.close()


# ── Derived stores ───────────────────────────────────────────────────────────

class TestVectorStoreIsScoped:
    """`log_embeddings` held every customer's log templates in one table.

    Templates are not innocuous: they carry table names, endpoints and internal
    hostnames with the variable parts stripped out.
    """

    @pytest.fixture
    def store(self, tmp_path):
        from denoiser.storage.vector_store import VectorStore

        return VectorStore(uri=str(tmp_path / "lancedb"))

    def _vec(self, seed: float):
        from denoiser.config import settings

        return [seed] * settings.embedding_dimension

    def test_search_returns_only_the_callers_own_templates(self, store):
        store.add_embeddings(
            ids=["a1"], vectors=[self._vec(0.1)],
            templates=["SELECT * FROM acme_payroll WHERE <*>"],
            sources=["acme.log"], timestamps=[0], tenant_id=1,
        )
        store.add_embeddings(
            ids=["g1"], vectors=[self._vec(0.1)],
            templates=["SELECT * FROM globex_secrets WHERE <*>"],
            sources=["globex.log"], timestamps=[0], tenant_id=2,
        )

        found = store.search(self._vec(0.1), tenant_id=1, limit=10)
        templates = [r["template"] for r in found]
        assert "SELECT * FROM acme_payroll WHERE <*>" in templates
        assert "SELECT * FROM globex_secrets WHERE <*>" not in templates

    def test_a_write_without_an_owner_is_refused(self, store):
        assert store.add_embeddings(
            ids=["x"], vectors=[self._vec(0.2)], templates=["t"],
            sources=["s"], timestamps=[0], tenant_id=None,
        ) is False

    def test_offboarding_removes_only_that_tenants_vectors(self, store):
        for tenant in (1, 2):
            store.add_embeddings(
                ids=[f"id{tenant}"], vectors=[self._vec(0.3)], templates=[f"t{tenant}"],
                sources=["s"], timestamps=[0], tenant_id=tenant,
            )
        assert store.delete_tenant(1) == 1
        assert store.search(self._vec(0.3), tenant_id=1) == []
        assert len(store.search(self._vec(0.3), tenant_id=2)) == 1


class TestArchivesArePartitionedByTenant:
    """One gzip per run held every customer's rows, so none could be deleted."""

    def test_object_key_carries_the_tenant(self):
        from denoiser.storage.archiver import archive_object_key

        assert archive_object_key("logs_t7_1700000000.jsonl.gz") == (
            "archive/logs/tenant=7/logs_t7_1700000000.jsonl.gz"
        )
        assert archive_object_key("traces_t7_1700000000.jsonl.gz") == (
            "archive/traces/tenant=7/traces_t7_1700000000.jsonl.gz"
        )

    def test_unattributed_rows_get_their_own_bucket(self):
        from denoiser.storage.archiver import UNKNOWN_TENANT, _tenant_key

        assert _tenant_key(None) == UNKNOWN_TENANT
        assert _tenant_key("") == UNKNOWN_TENANT
        # A tenant id is interpolated into a filename and an S3 key, so anything
        # that is not plainly alphanumeric is quarantined rather than trusted.
        assert _tenant_key("../../etc") == UNKNOWN_TENANT
        assert _tenant_key(12) == "12"
