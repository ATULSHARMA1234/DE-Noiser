"""add plans, subscriptions and processed webhook events

There was no commercial layer at all: no plan, no subscription, no invoice, no
webhook. `billing_meters` counted bytes and nothing turned bytes into revenue,
and `Tenant.tier` gated exactly two things (a request quota and a retention
window) while every actual feature was free to everyone.

Three tables:

  * `plans` — what can be bought. Priced per GB ingested, which is what the
    platform already meters and what its own costs track. Money is integer
    minor units; a float price accumulates rounding error across a month of
    usage lines and does not let you choose the direction.
  * `subscriptions` — one per workspace, holding the provider's view of status.
    Entitlement reads this, never `Tenant.tier`: a tier is a label somebody set,
    a status is whether the last invoice cleared.
  * `processed_webhook_events` — the idempotency ledger. Stripe retries for
    three days on a non-2xx and redelivers on a network hiccup where it did
    return one. The unique constraint is what makes a replay a no-op; an `if`
    is not, because two concurrent retries both pass it.

**The plans table is created empty, deliberately.**

Entitlement enforcement switches on when plan rows exist (see
`denoiser.api.entitlements.licensing_active`), so that an unlicensed
self-hosted install keeps working exactly as it did rather than losing SSO,
runbooks and tracing behind a 402 it has no way to pay — there is no payment
provider configured to pay *through*.

Seeding plans here would have defeated that entirely: every deployment that ran
the migration would have licensing switched on by the migration itself, and
every existing workspace would have lost those features on upgrade. Which is
precisely what happened the first time this was run against a real database.

Provisioning plans is the deliberate act that starts charging. It is done with
`scripts/seed_plans.py`, by an operator who means it.

Revision ID: d5f81b30e7a2
Revises: c8d94a2f60b3
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "d5f81b30e7a2"
down_revision = "c8d94a2f60b3"
branch_labels = None
depends_on = None


FEATURES = ("enterprise_identity", "automation", "distributed_tracing", "extended_retention")


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("included_gb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overage_price_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("base_price_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(), nullable=False, server_default="usd"),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("provider_price_id", sa.String(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_plans_id"), "plans", ["id"])
    op.create_index(op.f("ix_plans_slug"), "plans", ["slug"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="stripe"),
        sa.Column("provider_customer_id", sa.String(), nullable=True),
        sa.Column("provider_subscription_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="trialing"),
        sa.Column("current_period_start", sa.DateTime(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # One live subscription per workspace: two would make "which plan are
        # they on" a question with two answers.
        sa.UniqueConstraint("tenant_id", name="uq_subscriptions_tenant"),
    )
    op.create_index(op.f("ix_subscriptions_id"), "subscriptions", ["id"])
    op.create_index(op.f("ix_subscriptions_tenant_id"), "subscriptions", ["tenant_id"])
    op.create_index(
        op.f("ix_subscriptions_provider_customer_id"), "subscriptions", ["provider_customer_id"]
    )
    op.create_index(
        op.f("ix_subscriptions_provider_subscription_id"),
        "subscriptions", ["provider_subscription_id"],
    )

    op.create_table(
        "processed_webhook_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="stripe"),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # The whole point of the table.
        sa.UniqueConstraint("provider", "event_id", name="uq_processed_webhook_events"),
    )
    op.create_index(op.f("ix_processed_webhook_events_id"), "processed_webhook_events", ["id"])
    op.create_index(
        op.f("ix_processed_webhook_events_event_id"), "processed_webhook_events", ["event_id"]
    )


def downgrade() -> None:
    op.drop_table("processed_webhook_events")
    op.drop_table("subscriptions")
    op.drop_table("plans")
