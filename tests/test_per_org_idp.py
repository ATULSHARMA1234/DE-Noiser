"""The last two items left open by the multi-customer audit.

**One IdP per deployment.** OIDC and SAML were configured from deployment-wide
environment variables, so a shared deployment offered exactly one identity
provider. Domain routing decided which organisation a federated identity landed
in; it did not let two companies each bring their own Okta for interactive
sign-in.

**Asynchronous ClickHouse deletion.** `ALTER TABLE … DELETE` is *accepted*, not
applied, when a tenant purge returns. The endpoint's 200 therefore meant
"queued" while an erasure certificate has to mean "gone" — minutes or hours
apart on a large table.

The routing tests below carry the most weight. Deciding *whose* certificate
verifies an assertion is an authentication decision, and getting it from the
wrong place lets an attacker choose which organisation they are verified
against.
"""

from __future__ import annotations

import base64

import pytest

from denoiser.api import idp_registry
from denoiser.storage.db import SessionLocal, Tenant, TenantIdentityProvider


@pytest.fixture
def db():
    from denoiser.storage.db import init_db

    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.query(TenantIdentityProvider).delete()
        session.commit()
        session.close()


@pytest.fixture
def two_orgs(db):
    """Two customers, each owning a domain — the shape of a shared deployment.

    Torn down rather than left behind, and that matters more than it looks:
    registering *any* domain switches identity routing deployment-wide from
    "fall back to the first tenant" to "refuse an unregistered domain". Leaking
    these rows into the shared test database makes unrelated SSO and SCIM tests
    fail with a 403 that has nothing to do with what they are asserting.
    """
    made = []
    for name, domain in (("acme-idp", "acme-idp.test"), ("globex-idp", "globex-idp.test")):
        tenant = Tenant(name=name, sso_domains=[domain])
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        made.append(tenant)

    yield made

    for tenant in made:
        db.query(TenantIdentityProvider).filter(
            TenantIdentityProvider.tenant_id == tenant.id
        ).delete()
        db.delete(tenant)
    db.commit()


# ── Storage and fallback ─────────────────────────────────────────────────────

class TestProviderStorage:
    def test_an_organisation_with_no_provider_falls_back_to_the_deployment(self, db, two_orgs):
        """A single-customer install must keep working with no rows at all."""
        acme, _ = two_orgs
        assert idp_registry.get_provider(db, acme.id, idp_registry.SAML) is None

    def test_secrets_are_encrypted_at_rest(self, db, two_orgs):
        acme, _ = two_orgs
        idp_registry.upsert_provider(
            db, acme.id, idp_registry.OIDC,
            oidc_issuer="https://acme.okta.com",
            oidc_client_id="client-a",
            oidc_client_secret="super-secret-value",
        )
        row = db.query(TenantIdentityProvider).filter(
            TenantIdentityProvider.tenant_id == acme.id
        ).first()
        assert "super-secret-value" not in (row.oidc_client_secret or "")

        resolved = idp_registry.oidc_settings_for(db, acme.id)
        assert resolved["client_secret"] == "super-secret-value"

    def test_an_update_without_a_secret_keeps_the_stored_one(self, db, two_orgs):
        """Otherwise changing an issuer silently blanks the secret, and sign-in
        breaks at the next login rather than at the moment of the edit."""
        acme, _ = two_orgs
        idp_registry.upsert_provider(
            db, acme.id, idp_registry.OIDC,
            oidc_issuer="https://acme.okta.com",
            oidc_client_secret="keep-me",
        )
        idp_registry.upsert_provider(
            db, acme.id, idp_registry.OIDC, oidc_issuer="https://acme2.okta.com"
        )
        assert idp_registry.oidc_settings_for(db, acme.id)["client_secret"] == "keep-me"
        assert idp_registry.oidc_settings_for(db, acme.id)["issuer"] == "https://acme2.okta.com"

    def test_describe_never_returns_a_secret(self, db, two_orgs):
        acme, _ = two_orgs
        provider = idp_registry.upsert_provider(
            db, acme.id, idp_registry.OIDC,
            oidc_issuer="https://acme.okta.com",
            oidc_client_secret="do-not-leak",
        )
        body = idp_registry.describe(provider)
        assert body["client_secret_set"] is True
        assert "do-not-leak" not in str(body)

    def test_a_disabled_provider_is_not_used(self, db, two_orgs):
        acme, _ = two_orgs
        idp_registry.upsert_provider(
            db, acme.id, idp_registry.SAML,
            enabled=False,
            saml_idp_entity_id="https://acme.idp/metadata",
        )
        assert idp_registry.get_provider(db, acme.id, idp_registry.SAML) is None

    def test_an_unknown_protocol_is_refused(self, db, two_orgs):
        acme, _ = two_orgs
        with pytest.raises(ValueError):
            idp_registry.upsert_provider(db, acme.id, "ldap")


# ── Routing: whose certificate verifies this assertion ───────────────────────

class TestInboundRouting:
    def _configure(self, db, tenant, issuer, certificate="CERT-FOR-" ):
        return idp_registry.upsert_provider(
            db, tenant.id, idp_registry.SAML,
            saml_idp_entity_id=issuer,
            saml_idp_sso_url=f"{issuer}/sso",
            saml_idp_certificate=f"{certificate}{tenant.name}",
        )

    def test_two_organisations_can_each_bring_their_own_idp(self, db, two_orgs):
        """The whole point: this was impossible before."""
        acme, globex = two_orgs
        self._configure(db, acme, "https://acme.okta.com/saml")
        self._configure(db, globex, "https://globex.pingid.com/saml")

        acme_config, acme_tenant = idp_registry.saml_config_for_issuer(
            db, "https://acme.okta.com/saml"
        )
        globex_config, globex_tenant = idp_registry.saml_config_for_issuer(
            db, "https://globex.pingid.com/saml"
        )

        assert acme_tenant == acme.id
        assert globex_tenant == globex.id
        assert acme_config.idp_certificate != globex_config.idp_certificate

    def test_an_assertion_is_verified_against_its_own_issuers_certificate(self, db, two_orgs):
        """Not against whichever organisation happens to sort first, and not
        against one named by a query parameter."""
        acme, globex = two_orgs
        self._configure(db, acme, "https://acme.okta.com/saml")
        self._configure(db, globex, "https://globex.pingid.com/saml")

        config, _ = idp_registry.saml_config_for_issuer(db, "https://globex.pingid.com/saml")
        assert config.idp_certificate.endswith("globex-idp")

    def test_an_unknown_issuer_falls_back_rather_than_guessing(self, db, two_orgs):
        """Guessing would verify a stranger's assertion against a real
        customer's certificate."""
        acme, _ = two_orgs
        self._configure(db, acme, "https://acme.okta.com/saml")

        _config, tenant_id = idp_registry.saml_config_for_issuer(db, "https://evil.example/saml")
        assert tenant_id is None

    def test_the_issuer_is_read_from_the_assertion_not_from_the_caller(self):
        """`peek_saml_issuer` is routing-only, and the value it returns is later
        re-checked against the certificate it selected — so a forged issuer can
        only pick a certificate that will fail to verify it."""
        xml = (
            '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
            'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">'
            "<saml:Issuer>https://acme.okta.com/saml</saml:Issuer>"
            "<saml:Assertion><saml:Issuer>https://real.okta.com/saml</saml:Issuer>"
            "</saml:Assertion></samlp:Response>"
        )
        encoded = base64.b64encode(xml.encode()).decode()
        # The assertion-level issuer wins, matching what verification treats as
        # authoritative — routing and verification must agree on the claim.
        assert idp_registry.peek_saml_issuer(encoded) == "https://real.okta.com/saml"

    @pytest.mark.parametrize("garbage", [None, "", "not base64!!", base64.b64encode(b"<nope").decode()])
    def test_unparseable_input_yields_no_routing_hint_rather_than_an_error(self, garbage):
        """This runs before anything is authenticated, over hostile input."""
        assert idp_registry.peek_saml_issuer(garbage) is None


class TestOutboundRouting:
    def test_a_login_hint_can_be_a_domain(self, db, two_orgs):
        acme, _ = two_orgs
        assert idp_registry.tenant_for_hint(db, "acme-idp.test").id == acme.id

    def test_a_login_hint_can_be_a_whole_email_address(self, db, two_orgs):
        acme, _ = two_orgs
        assert idp_registry.tenant_for_hint(db, "someone@acme-idp.test").id == acme.id

    def test_a_login_hint_can_be_an_organisation_name(self, db, two_orgs):
        _, globex = two_orgs
        assert idp_registry.tenant_for_hint(db, "GLOBEX-IDP").id == globex.id

    def test_an_unmatched_hint_falls_back_to_the_deployment_provider(self, db, two_orgs):
        """A forged hint is harmless outbound: the worst it achieves is a
        redirect to somebody else's IdP, which will not authenticate you."""
        assert idp_registry.tenant_for_hint(db, "nobody.example") is None


class TestOffboardingRemovesTheProvider:
    def test_the_idp_config_is_classified_as_customer_owned(self):
        """Left behind, it would keep routing assertions to a tenant that no
        longer exists — and it holds that customer's client secret."""
        from denoiser.api.tenancy import TENANT_SCOPED_MODELS

        assert "TenantIdentityProvider" in TENANT_SCOPED_MODELS


# ── Erasure is certified against completion, not acceptance ──────────────────

class FakeResult:
    def __init__(self, rows):
        self.result_rows = rows


class MutationClient:
    """A ClickHouse whose mutations finish only when the test says so."""

    def __init__(self, done=False):
        self.done = done
        self.commands = []

    def command(self, sql, parameters=None):
        self.commands.append((sql, parameters or {}))

    def query(self, sql, parameters=None):
        if "system.mutations" not in sql:
            return FakeResult([])
        if "mutation_id IN" in sql:
            return FakeResult(
                [(mid, "semantic_logs", self.done, "") for mid in parameters["ids"]]
            )
        return FakeResult(
            [("mutation-1", "semantic_logs", False), ("mutation-2", "semantic_traces", False)]
        )


def store(client):
    from denoiser.storage.clickhouse_store import ClickHouseStore

    return ClickHouseStore(client=client)


class TestErasureIsVerifiable:
    def test_submitting_a_purge_returns_the_mutations_to_track_it_by(self):
        client = MutationClient()
        result = store(client).submit_tenant_deletion("7")

        assert result["submitted"] is True
        assert result["tables"] == ["semantic_logs", "semantic_traces"]
        assert {m["mutation_id"] for m in result["mutations"]} == {"mutation-1", "mutation-2"}

    def test_a_pending_mutation_means_the_erasure_is_not_complete(self):
        """The rows are still on disk in parts that have not been rewritten.
        Issuing a certificate here would be certifying a queued request."""
        status = store(MutationClient(done=False)).mutation_status(["mutation-1"])
        assert status["complete"] is False
        assert status["pending"] == 1

    def test_completion_is_only_reported_once_clickhouse_says_so(self):
        status = store(MutationClient(done=True)).mutation_status(["mutation-1"])
        assert status["complete"] is True
        assert status["pending"] == 0

    def test_a_purge_with_no_rows_to_delete_is_complete_not_indeterminate(self):
        assert store(MutationClient()).mutation_status([])["complete"] is True

    def test_a_mutation_that_has_left_the_table_counts_as_applied(self):
        """ClickHouse drops finished mutations from system.mutations. Treating
        the absence as pending would leave a real erasure permanently
        uncertifiable."""
        class Empty(MutationClient):
            def query(self, sql, parameters=None):
                return FakeResult([])

        status = store(Empty()).mutation_status(["mutation-gone"])
        assert status["complete"] is True

    def test_an_unreachable_clickhouse_is_unverified_not_complete(self):
        """The one answer that must never be given by default."""
        class Down(MutationClient):
            def query(self, sql, parameters=None):
                raise ConnectionError("clickhouse is down")

        status = store(Down()).mutation_status(["mutation-1"])
        assert status["complete"] is False
        assert "error" in status

    def test_the_erasure_record_survives_the_purge_it_certifies(self):
        """It is the evidence the deletion happened; purging it with the
        customer would destroy the only proof of the erasure."""
        from denoiser.api.tenancy import GLOBAL_MODELS

        assert "ErasureRecord" in GLOBAL_MODELS
