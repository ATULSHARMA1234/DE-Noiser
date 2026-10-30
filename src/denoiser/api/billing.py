"""Taking money, and recording what was agreed.

There was no billing here at all — no provider, no subscription state, no
invoice, no webhook. `BillingMeter` counted bytes and nothing turned bytes into
revenue.

Design notes worth stating once, because each of them is a way this goes wrong:

**The provider is the source of truth for status.** This service never decides
that a subscription is active; it records what the provider said. Anything else
drifts, and the drift is always discovered during a billing dispute.

**Every webhook is idempotent.** Stripe retries for three days on a non-2xx and
will redeliver on a network hiccup where it did get one. The `event.id` is
claimed in the database before the handler runs, and the unique constraint —
not an `if` — is what makes a replay a no-op.

**The signature is verified before the body is parsed.** An unverified webhook
endpoint is an unauthenticated write to the table that decides who has paid.

**The SDK is imported lazily.** This is an on-premise product; an operator who
never takes card payments should not need `stripe` installed to boot the API.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.api.entitlements import ENTITLING_STATUSES, features_for
from denoiser.api.scope import tenant_predicate
from denoiser.logging import get_logger
from denoiser.storage.db import (
    Plan,
    ProcessedWebhookEvent,
    Subscription,
    Tenant,
    get_db,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# ── Schemas ─────────────────────────────────────────────────────────────────

class PlanSchema(BaseModel):
    slug: str
    name: str
    included_gb: int
    overage_price_minor: int
    base_price_minor: int
    currency: str
    features: list
    retention_days: int

    model_config = ConfigDict(from_attributes=True)


class SubscriptionSchema(BaseModel):
    plan: str
    status: str
    current_period_end: datetime | None
    cancel_at_period_end: bool
    features: list[str]

    model_config = ConfigDict(from_attributes=True)


class CheckoutRequest(BaseModel):
    plan_slug: str
    success_url: str
    cancel_url: str


# ── Provider ────────────────────────────────────────────────────────────────

def _stripe():
    """The Stripe SDK, configured, or a 503 explaining what is missing.

    Lazy because this is an on-premise product: a deployment that invoices its
    customers by hand should not fail to start over a missing payment SDK.
    """
    secret = os.getenv("STRIPE_SECRET_KEY")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Card payment is not configured on this deployment.",
        )
    try:
        import stripe
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Card payment is not available: the stripe package is not installed.",
        )
    stripe.api_key = secret
    return stripe


# ── Read ────────────────────────────────────────────────────────────────────

@router.get("/plans", response_model=list[PlanSchema])
def list_plans(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """The plans on offer — a price list, for anyone signed in.

    Authenticated rather than public. The content is not sensitive, but an
    unauthenticated endpoint on an on-premise deployment confirms the
    deployment exists and what its operator charges, to anyone who can reach
    the port. There is no reason to hand that out for free.
    """
    return db.query(Plan).filter(Plan.is_public == True).order_by(Plan.base_price_minor).all()


@router.get("/subscription", response_model=SubscriptionSchema)
def current_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """What this workspace is on, and what that currently grants.

    `features` is returned so the console can hide what is not included — as a
    convenience, not as the enforcement. Enforcement is `require_feature` on the
    routes themselves; a client that ignores this list gets a 402, not data.
    """
    subscription = (
        db.query(Subscription)
        .filter(tenant_predicate(Subscription, current_user.tenant_id))
        .first()
    )
    granted = sorted(features_for(db, current_user.tenant_id))

    if subscription is None:
        return SubscriptionSchema(
            plan="free", status="none", current_period_end=None,
            cancel_at_period_end=False, features=granted,
        )

    plan = db.query(Plan).filter(Plan.id == subscription.plan_id).first()
    return SubscriptionSchema(
        plan=plan.slug if plan else "unknown",
        status=subscription.status,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=subscription.cancel_at_period_end,
        features=granted,
    )


# ── Write ───────────────────────────────────────────────────────────────────

@router.post("/checkout")
def create_checkout_session(
    payload: CheckoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Start a hosted Checkout session for this workspace.

    Hosted rather than a card form of our own: it keeps card data out of this
    process entirely, which is the difference between SAQ A and a PCI audit.
    """
    stripe = _stripe()

    plan = db.query(Plan).filter(Plan.slug == payload.plan_slug).first()
    if plan is None or not plan.provider_price_id:
        raise HTTPException(status_code=404, detail="No such plan")

    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=400, detail="This account is not part of a workspace")

    subscription = (
        db.query(Subscription).filter(Subscription.tenant_id == tenant.id).first()
    )
    customer_id = subscription.provider_customer_id if subscription else None

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id or None,
            customer_email=None if customer_id else current_user.email,
            line_items=[{"price": plan.provider_price_id}],
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
            # Carried back on every webhook for this subscription, so the
            # handler never has to guess which workspace an event belongs to.
            # Matching on email would attach a payment to the wrong workspace
            # the first time somebody uses the same address for two of them.
            subscription_data={"metadata": {"tenant_id": str(tenant.id), "plan_slug": plan.slug}},
            metadata={"tenant_id": str(tenant.id), "plan_slug": plan.slug},
        )
    except Exception as e:
        logger.error("Could not create a checkout session for tenant %s: %s", tenant.id, e)
        raise HTTPException(status_code=502, detail="The payment provider could not be reached.")

    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/portal")
def create_portal_session(
    return_url: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """A link to the provider's billing portal.

    Plan changes, cancellation, card updates and invoice history all live there.
    Proration, dunning and refunds are theirs to compute — reimplementing any of
    that is how a rounding difference becomes a chargeback.
    """
    stripe = _stripe()

    subscription = (
        db.query(Subscription)
        .filter(tenant_predicate(Subscription, current_user.tenant_id))
        .first()
    )
    if subscription is None or not subscription.provider_customer_id:
        raise HTTPException(status_code=404, detail="This workspace has no billing account yet")

    try:
        session = stripe.billing_portal.Session.create(
            customer=subscription.provider_customer_id, return_url=return_url
        )
    except Exception as e:
        logger.error("Could not create a portal session: %s", e)
        raise HTTPException(status_code=502, detail="The payment provider could not be reached.")

    return {"portal_url": session.url}


# ── Webhook ─────────────────────────────────────────────────────────────────

def _claim_event(db: Session, provider: str, event_id: str, event_type: str) -> bool:
    """Record that this event is being handled. False if it already was.

    The unique constraint does the work. Checking for the row first and then
    inserting is check-then-act, and two concurrent deliveries of the same
    event — which is exactly what a retry storm looks like — both pass the
    check.
    """
    db.add(ProcessedWebhookEvent(provider=provider, event_id=event_id, event_type=event_type))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _apply_subscription_event(db: Session, obj: dict) -> None:
    """Write the provider's view of a subscription into our own table."""
    metadata = obj.get("metadata") or {}
    tenant_id = metadata.get("tenant_id")
    if not tenant_id:
        logger.error(
            "Subscription %s carries no tenant_id; refusing to guess which "
            "workspace it belongs to", obj.get("id"),
        )
        return

    plan = db.query(Plan).filter(Plan.slug == metadata.get("plan_slug")).first()
    if plan is None:
        logger.error("Subscription %s names unknown plan %r", obj.get("id"), metadata.get("plan_slug"))
        return

    subscription = (
        db.query(Subscription).filter(Subscription.tenant_id == int(tenant_id)).first()
    )
    if subscription is None:
        subscription = Subscription(tenant_id=int(tenant_id))
        db.add(subscription)

    subscription.plan_id = plan.id
    subscription.provider = "stripe"
    subscription.provider_customer_id = obj.get("customer") or subscription.provider_customer_id
    subscription.provider_subscription_id = obj.get("id")
    # Stored as the provider gave it. Mapping it into a local vocabulary means a
    # status we have not seen becomes one we have, silently and in the
    # customer's favour.
    subscription.status = obj.get("status", "incomplete")
    subscription.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))

    for field, key in (("current_period_start", "current_period_start"),
                       ("current_period_end", "current_period_end")):
        epoch = obj.get(key)
        if epoch:
            setattr(subscription, field, datetime.fromtimestamp(epoch, UTC).replace(tzinfo=None))

    db.commit()
    logger.info(
        "Subscription for tenant %s is now %s on plan %s",
        tenant_id, subscription.status, plan.slug,
    )


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Receive subscription lifecycle events.

    Unauthenticated by URL and authenticated by signature — this is the endpoint
    that decides who has paid, so the signature check comes before anything
    reads the body.
    """
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        # Not 404: an operator who has configured Checkout but forgotten the
        # webhook secret needs to see that this is a misconfiguration, because
        # the symptom otherwise is subscriptions that never activate.
        raise HTTPException(status_code=503, detail="Webhook handling is not configured.")

    payload = await request.body()
    signature = request.headers.get("stripe-signature")

    try:
        import stripe
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except ImportError:
        raise HTTPException(status_code=503, detail="The stripe package is not installed.")
    except Exception as e:
        # Deliberately terse: a verification failure is either a
        # misconfiguration or someone probing, and neither should be told which.
        logger.warning("Rejected a webhook with an invalid signature: %s", e)
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_id = event["id"]
    event_type = event["type"]

    if not _claim_event(db, "stripe", event_id, event_type):
        # Already handled. 200, so the provider stops retrying.
        logger.info("Ignoring replayed event %s (%s)", event_id, event_type)
        return {"status": "already_processed", "event_id": event_id}

    obj = event["data"]["object"]

    if event_type.startswith("customer.subscription."):
        _apply_subscription_event(db, obj)
    elif event_type == "invoice.payment_failed":
        # Recorded, not acted on. Dunning is the provider's job, and it will
        # move the subscription to past_due and then unpaid on its own schedule;
        # `past_due` deliberately keeps access (see api.entitlements).
        logger.warning("Payment failed for customer %s", obj.get("customer"))
    elif event_type == "invoice.paid":
        logger.info("Invoice paid for customer %s", obj.get("customer"))
    else:
        logger.info("No handler for event type %s; recorded and ignored", event_type)

    return {"status": "ok", "event_id": event_id}


# ── Usage reporting ─────────────────────────────────────────────────────────

def report_usage_for_day(db: Session, day) -> dict:
    """Push each tenant's metered volume to the provider for ``day``.

    Called from the metering pass, after the meters are written, so what is
    billed is exactly what was recorded — rather than a second, independently
    computed number that can disagree with the one the customer sees.

    Reports gigabytes, rounded up: a partial gigabyte is a gigabyte, stated
    plainly here rather than hidden in a float.
    """
    from denoiser.storage.db import BillingMeter

    summary = {"reported": 0, "skipped": 0, "failed": 0}
    if not os.getenv("STRIPE_SECRET_KEY"):
        return summary

    try:
        import stripe
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    except ImportError:
        return summary

    day_start = datetime(day.year, day.month, day.day)
    meters = db.query(BillingMeter).filter(BillingMeter.date == day_start).all()

    for meter in meters:
        subscription = (
            db.query(Subscription).filter(Subscription.tenant_id == meter.tenant_id).first()
        )
        if subscription is None or subscription.status not in ENTITLING_STATUSES:
            summary["skipped"] += 1
            continue
        if not subscription.provider_subscription_id:
            summary["skipped"] += 1
            continue

        gigabytes = -(-(meter.total_bytes_ingested or 0) // (1024 ** 3))  # ceil
        try:
            stripe.billing.MeterEvent.create(
                event_name="log_bytes_ingested",
                payload={
                    "stripe_customer_id": subscription.provider_customer_id,
                    "value": str(gigabytes),
                },
                # The provider deduplicates on this, so a re-run of the metering
                # pass for the same day does not bill the customer twice.
                identifier=f"{meter.tenant_id}-{day.isoformat()}",
            )
            summary["reported"] += 1
        except Exception as e:
            summary["failed"] += 1
            logger.error("Could not report usage for tenant %s: %s", meter.tenant_id, e)

    return summary
