"""Money, and who is allowed to use what.

There was no commercial layer at all before this: no plan, no subscription, no
webhook, and `Tenant.tier` gated exactly two things while every actual feature
was free to every account.

The three properties worth testing are the three that cost money when wrong:

  * a replayed webhook must not apply twice — the provider retries for days;
  * a lapsed subscription must actually lose access — server-side, not by
    hiding a nav item;
  * a cancelled-but-paid subscription must keep access to the end of the
    period, because the customer paid for it.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from denoiser.api.entitlements import (
    FEATURE_AUTOMATION,
    FEATURE_SSO,
    features_for,
    retention_days_for,
)
from denoiser.storage.db import Plan, ProcessedWebhookEvent, SessionLocal, Subscription, Tenant


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
def plans(db):
    """The seeded plans, created here because tests run on create_all, not migrations.

    Torn down afterwards. The presence of *any* plan row is what switches
    entitlement enforcement on for the whole deployment (see
    `entitlements.licensing_active`), so leaving these behind would make every
    other test in the session run against a licensed deployment and get a 402
    from routes it expects to reach.
    """
    wanted = {
        "free": ([], 7),
        "pro": ([FEATURE_AUTOMATION], 30),
        "enterprise": ([FEATURE_SSO, FEATURE_AUTOMATION], 90),
    }
    out = {}
    for slug, (features, retention) in wanted.items():
        plan = db.query(Plan).filter(Plan.slug == slug).first()
        if plan is None:
            plan = Plan(
                slug=slug, name=slug.title(), features=features,
                retention_days=retention, included_gb=5, currency="usd",
            )
            db.add(plan)
        else:
            plan.features = features
            plan.retention_days = retention
        out[slug] = plan
    db.commit()

    yield out

    db.query(Subscription).delete()
    db.query(Plan).delete()
    db.commit()


@pytest.fixture
def tenant(db):
    workspace = Tenant(name=f"billing-fixture-{datetime.now(UTC).timestamp()}")
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


def _subscribe(db, tenant, plan, status, **kwargs):
    db.query(Subscription).filter(Subscription.tenant_id == tenant.id).delete()
    subscription = Subscription(
        tenant_id=tenant.id, plan_id=plan.id, status=status,
        provider_subscription_id=f"sub_{tenant.id}", **kwargs,
    )
    db.add(subscription)
    db.commit()
    return subscription


class TestAnUnlicensedDeploymentIsUnaffected:
    """No plans provisioned means nothing is being sold here.

    A self-hosted install that upgrades into this code must not silently lose
    SSO, runbooks and tracing behind a 402 it has no way to pay — there is no
    provider configured to pay through. Gating switches on when plans exist,
    which is a deliberate act nobody performs by accident.
    """

    def test_everything_is_granted_when_no_plans_exist(self, db, tenant):
        from denoiser.api.entitlements import ALL_FEATURES, licensing_active

        db.query(Subscription).delete()
        db.query(Plan).delete()
        db.commit()

        assert licensing_active(db) is False
        assert features_for(db, tenant.id) == frozenset(ALL_FEATURES)

    def test_provisioning_a_plan_turns_enforcement_on(self, db, tenant, plans):
        from denoiser.api.entitlements import licensing_active

        assert licensing_active(db) is True
        assert features_for(db, tenant.id) == frozenset()


class TestEntitlementFollowsTheSubscription:
    def test_no_subscription_grants_nothing_paid(self, db, tenant, plans):
        assert features_for(db, tenant.id) == frozenset()

    def test_an_active_subscription_grants_its_plan(self, db, tenant, plans):
        _subscribe(db, tenant, plans["enterprise"], "active")
        assert FEATURE_SSO in features_for(db, tenant.id)
        assert FEATURE_AUTOMATION in features_for(db, tenant.id)

    def test_a_cheaper_plan_does_not_grant_a_dearer_plans_feature(self, db, tenant, plans):
        _subscribe(db, tenant, plans["pro"], "active")
        granted = features_for(db, tenant.id)
        assert FEATURE_AUTOMATION in granted
        assert FEATURE_SSO not in granted

    def test_a_failed_payment_keeps_access_while_dunning_runs(self, db, tenant, plans):
        """`past_due` is deliberately still entitling.

        The customer has a failed card and a dunning email, not an intention to
        leave. Cutting off their observability platform at that moment is how a
        billing problem becomes an incident they cannot see.
        """
        _subscribe(db, tenant, plans["pro"], "past_due")
        assert FEATURE_AUTOMATION in features_for(db, tenant.id)

    @pytest.mark.parametrize("status", ["canceled", "unpaid", "incomplete_expired", "paused"])
    def test_a_finished_subscription_grants_nothing(self, db, tenant, plans, status):
        _subscribe(db, tenant, plans["enterprise"], status)
        assert features_for(db, tenant.id) == frozenset()

    def test_an_unrecognised_status_fails_closed(self, db, tenant, plans):
        """A status this code has not seen must not be read as "probably fine"."""
        _subscribe(db, tenant, plans["enterprise"], "some_new_stripe_status")
        assert features_for(db, tenant.id) == frozenset()

    def test_a_subscription_pointing_at_a_deleted_plan_fails_closed(self, db, tenant, plans):
        subscription = _subscribe(db, tenant, plans["pro"], "active")
        subscription.plan_id = 999_999
        db.commit()
        assert features_for(db, tenant.id) == frozenset()

    def test_tier_alone_grants_nothing(self, db, tenant, plans):
        """The old gate. A label somebody typed is not a payment."""
        tenant.tier = "enterprise"
        db.commit()
        assert features_for(db, tenant.id) == frozenset()


class TestRetentionFollowsThePlan:
    def test_the_plan_sets_the_window(self, db, tenant, plans):
        _subscribe(db, tenant, plans["enterprise"], "active")
        assert retention_days_for(db, tenant.id) == 90

    def test_a_lapsed_subscription_falls_back_to_the_default(self, db, tenant, plans):
        _subscribe(db, tenant, plans["enterprise"], "canceled")
        assert retention_days_for(db, tenant.id, default=7) == 7


class TestWebhookIdempotency:
    def test_an_event_is_claimed_once(self, db):
        from denoiser.api.billing import _claim_event

        event_id = f"evt_{datetime.now(UTC).timestamp()}"
        assert _claim_event(db, "stripe", event_id, "customer.subscription.updated") is True
        # The provider retries for three days on a non-2xx, and redelivers on a
        # network hiccup where it did return one.
        assert _claim_event(db, "stripe", event_id, "customer.subscription.updated") is False

    def test_the_ledger_records_it_once(self, db):
        from denoiser.api.billing import _claim_event

        event_id = f"evt_ledger_{datetime.now(UTC).timestamp()}"
        _claim_event(db, "stripe", event_id, "invoice.paid")
        _claim_event(db, "stripe", event_id, "invoice.paid")

        assert db.query(ProcessedWebhookEvent).filter(
            ProcessedWebhookEvent.event_id == event_id
        ).count() == 1

    def test_a_replayed_subscription_event_does_not_apply_twice(self, db, tenant, plans):
        """The concrete harm: an upgrade applied twice, or a status flapped."""
        from denoiser.api.billing import _apply_subscription_event

        period_end = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
        event_object = {
            "id": "sub_replay_test",
            "customer": "cus_123",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": period_end,
            "metadata": {"tenant_id": str(tenant.id), "plan_slug": "pro"},
        }

        _apply_subscription_event(db, event_object)
        _apply_subscription_event(db, event_object)

        rows = db.query(Subscription).filter(Subscription.tenant_id == tenant.id).all()
        assert len(rows) == 1
        assert rows[0].status == "active"


class TestSubscriptionEventsAreAttributedSafely:
    def test_an_event_with_no_tenant_is_refused_rather_than_guessed(self, db, plans):
        """Matching on email would attach a payment to the wrong workspace."""
        from denoiser.api.billing import _apply_subscription_event

        before = db.query(Subscription).count()
        _apply_subscription_event(db, {
            "id": "sub_orphan", "customer": "cus_x", "status": "active",
            "metadata": {"plan_slug": "pro"},
        })
        assert db.query(Subscription).count() == before

    def test_an_event_naming_an_unknown_plan_is_refused(self, db, tenant, plans):
        from denoiser.api.billing import _apply_subscription_event

        before = db.query(Subscription).count()
        _apply_subscription_event(db, {
            "id": "sub_badplan", "customer": "cus_x", "status": "active",
            "metadata": {"tenant_id": str(tenant.id), "plan_slug": "platinum-deluxe"},
        })
        assert db.query(Subscription).count() == before

    def test_cancel_at_period_end_keeps_access(self, db, tenant, plans):
        """They paid through the period. They keep it through the period."""
        from denoiser.api.billing import _apply_subscription_event

        _apply_subscription_event(db, {
            "id": "sub_cancelling", "customer": "cus_y", "status": "active",
            "cancel_at_period_end": True,
            "current_period_end": int((datetime.now(UTC) + timedelta(days=12)).timestamp()),
            "metadata": {"tenant_id": str(tenant.id), "plan_slug": "pro"},
        })

        assert FEATURE_AUTOMATION in features_for(db, tenant.id)
        subscription = db.query(Subscription).filter(
            Subscription.tenant_id == tenant.id
        ).first()
        assert subscription.cancel_at_period_end is True


class TestGatingIsServerSide:
    """A hidden nav item is not an entitlement; the endpoint is still there."""

    @pytest.fixture
    def client(self):
        from denoiser.api.main import app
        return TestClient(app)

    @pytest.mark.parametrize("path", ["/runbooks", "/traces", "/slos"])
    def test_a_gated_route_is_not_reachable_unauthenticated(self, client, path):
        res = client.get(path)
        # Whatever the reason, it is not a 200 with data in it.
        assert res.status_code != 200, res.text

    def test_the_webhook_refuses_an_unsigned_body(self, client, monkeypatch):
        """This endpoint decides who has paid. It is authenticated by signature."""
        pytest.importorskip(
            "stripe", reason="signature verification needs the SDK (uv sync --extra billing)"
        )
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        res = client.post("/billing/webhook", json={"id": "evt_forged", "type": "invoice.paid"})
        assert res.status_code == 400
        assert "signature" in res.json()["detail"].lower()

    def test_the_webhook_is_unusable_without_the_sdk(self, client, monkeypatch):
        """The other half: absent the SDK, it refuses rather than accepting blind.

        This is the on-premise default — `stripe` is an optional extra — and the
        dangerous failure would be an endpoint that skips verification when it
        cannot verify.
        """
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        res = client.post("/billing/webhook", json={"id": "evt_x", "type": "invoice.paid"})
        assert res.status_code in (400, 503)
        assert res.status_code != 200

    def test_the_webhook_reports_misconfiguration_rather_than_404(self, client, monkeypatch):
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        res = client.post("/billing/webhook", json={})
        # An operator who configured Checkout but forgot the webhook secret has
        # subscriptions that never activate; 404 would hide the reason.
        assert res.status_code == 503
