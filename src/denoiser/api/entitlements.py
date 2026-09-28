"""What a customer is allowed to use, and why.

Before this, `Tenant.tier` decided exactly two things — a request quota and a
retention window — and every actual *feature* was available to every account.
SSO, SCIM, runbooks, tracing, SLOs: all of it, free. There was nothing to sell
that a free account did not already have.

Two rules hold this together, and both matter:

**Entitlement is decided from the subscription, not the tier.** A tier is a
label somebody set in an admin form. A status is what the payment provider says
about whether the last invoice cleared. Gate on the label and a customer whose
card fails keeps everything until a human notices.

**Gating is server-side, at the router dependency.** A hidden nav item is a
presentation choice, not an entitlement — the endpoint is still there, and the
person most likely to call it directly is exactly the person who does not want
to pay for it.

Grace is deliberate. `past_due` keeps access: the customer has a failed payment
and a dunning email, and cutting off their observability platform the hour their
card expires is how an incident becomes an outage. `unpaid` and `canceled` do
not — by then the provider has exhausted its retries.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from denoiser.api.auth import User, get_current_user
from denoiser.logging import get_logger
from denoiser.storage.db import Plan, Subscription, get_db

logger = get_logger(__name__)


# ── Features ────────────────────────────────────────────────────────────────

#: Enterprise identity: OIDC/SAML SSO, SCIM provisioning, per-tenant IdP.
FEATURE_SSO = "enterprise_identity"
#: Runbook execution and multi-channel alert routing.
FEATURE_AUTOMATION = "automation"
#: OTLP trace ingest and the trace explorer.
FEATURE_TRACING = "distributed_tracing"
#: Retention beyond the free window, SLO tracking and forecasting.
FEATURE_RETENTION_SLO = "extended_retention"

ALL_FEATURES = (FEATURE_SSO, FEATURE_AUTOMATION, FEATURE_TRACING, FEATURE_RETENTION_SLO)

#: What a deployment with no plan rows yet grants. An on-premise install that
#: has never been licensed should still analyse logs — the core product — while
#: the paid capabilities stay off until someone provisions a plan.
FREE_FEATURES: tuple[str, ...] = ()


# ── Status vocabulary ───────────────────────────────────────────────────────

#: Statuses that carry the plan's features.
#:
#: `past_due` is included on purpose: the invoice failed and dunning is running,
#: but the customer has not decided to leave and the provider has not given up.
#: Revoking an observability platform at that moment is how a billing problem
#: turns into an incident the customer cannot see.
ENTITLING_STATUSES = frozenset({"trialing", "active", "past_due"})

#: Statuses that carry nothing. The provider has finished retrying.
REVOKED_STATUSES = frozenset({"canceled", "unpaid", "incomplete_expired", "paused"})


def licensing_active(db: Session) -> bool:
    """Whether this deployment sells anything.

    A deployment with no plan rows has never been licensed: it is a self-hosted
    install, a development checkout, or an evaluation. Enforcing entitlement
    there would mean an upgrade silently removes SSO, runbooks and tracing from
    an installation that has been using them, with a 402 and no way to pay —
    there is no payment provider configured to pay *through*.

    So gating switches itself on the moment plans exist. Provisioning a plan is
    the deliberate act; nobody does it by accident, and until it happens this
    behaves exactly as it did before the commercial layer was added.

    This is a licensing decision, not a security one. Nothing here bypasses
    authentication, authorisation or tenant scoping — those apply regardless.
    """
    try:
        return db.query(Plan).limit(1).count() > 0
    except Exception as e:
        # A missing table means the migration has not run: unlicensed.
        logger.debug("Plan table unavailable, treating deployment as unlicensed: %s", e)
        return False


def features_for(db: Session, tenant_id: int | None) -> frozenset[str]:
    """Every feature slug this tenant may use right now.

    Fails closed *once licensing is active*. An unrecognised status, a
    subscription pointing at a plan that no longer exists, a database error —
    all of them yield the free feature set rather than the paid one. The failure
    mode of a bug here should be a support ticket, not unbilled usage.

    On an unlicensed deployment it fails open, deliberately. See
    `licensing_active`.
    """
    if not licensing_active(db):
        return frozenset(ALL_FEATURES)

    if tenant_id is None:
        return frozenset(FREE_FEATURES)

    try:
        subscription = (
            db.query(Subscription).filter(Subscription.tenant_id == tenant_id).first()
        )
    except Exception as e:
        logger.error("Could not read the subscription for tenant %s: %s", tenant_id, e)
        return frozenset(FREE_FEATURES)

    if subscription is None:
        return frozenset(FREE_FEATURES)

    if subscription.status not in ENTITLING_STATUSES:
        return frozenset(FREE_FEATURES)

    plan = db.query(Plan).filter(Plan.id == subscription.plan_id).first()
    if plan is None:
        logger.error(
            "Subscription %s points at plan %s, which does not exist",
            subscription.id, subscription.plan_id,
        )
        return frozenset(FREE_FEATURES)

    return frozenset(plan.features or ())


def retention_days_for(db: Session, tenant_id: int | None, *, default: int = 7) -> int:
    """How long this tenant's data is kept.

    Read from the plan when there is one. The tier table in `billing_worker`
    remains the fallback for deployments that have not been licensed, so an
    unlicensed install keeps working rather than deleting everything or nothing.
    """
    if tenant_id is None:
        return default
    try:
        subscription = (
            db.query(Subscription).filter(Subscription.tenant_id == tenant_id).first()
        )
        if subscription is None or subscription.status not in ENTITLING_STATUSES:
            return default
        plan = db.query(Plan).filter(Plan.id == subscription.plan_id).first()
        return int(plan.retention_days) if plan else default
    except Exception as e:
        logger.error("Could not resolve retention for tenant %s: %s", tenant_id, e)
        return default


def require_feature(feature: str):
    """FastAPI dependency: 402 unless the caller's plan includes ``feature``.

    402 Payment Required rather than 403 Forbidden. The distinction is real and
    the client acts on it differently: 403 means "you may not", which a user
    cannot resolve, and 402 means "this is not in your plan", which they can.

    Used as ``Depends(require_feature(FEATURE_SSO))`` beside the existing
    ``require_role(...)``, so authorisation and entitlement stay separate
    questions — being an ADMIN of an unpaid workspace is not the same as being
    a VIEWER of a paid one.
    """

    def dependency(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> User:
        if feature in features_for(db, current_user.tenant_id):
            return current_user
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"'{feature}' is not included in your current plan. "
                "Upgrade in Settings → Billing, or contact your account owner."
            ),
        )

    return dependency
