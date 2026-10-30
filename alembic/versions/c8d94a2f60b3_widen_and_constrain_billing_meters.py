"""widen the usage counters and give a tenant-day one row

Two defects in one small table, both of which cost money.

`total_bytes_ingested` was `INTEGER`. On PostgreSQL that is signed 32-bit, so it
holds 2,147,483,647 — a little over 2 GiB — and a customer sending more than that
in a day made the write raise `NumericValueOutOfRange`. The commit covering it
was the one at the end of the whole pass, so a single large customer discarded
every other tenant's meter for that day as well. `total_logs_ingested` had the
same ceiling at 2.1 billion records. Both, and the trace counter beside them,
become `BIGINT`.

There was also no uniqueness on `(tenant_id, date)`. Metering reads-then-writes
to stay idempotent, which is check-then-act: two beats — the module docstring in
`billing_worker` notes that a second one is possible — or a manual re-run
overlapping the scheduled one, and both processes see "no row yet" and both
insert. Summing a duplicated day double-bills the customer, which is the worse
direction to be wrong in.

The duplicate cleanup keeps the highest `id` per tenant-day: later rows were
written by later passes, and a later pass has seen more of the day.

Revision ID: c8d94a2f60b3
Revises: b7c3f5a91e42
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "c8d94a2f60b3"
down_revision = "b7c3f5a91e42"
branch_labels = None
depends_on = None


COUNTERS = ("total_logs_ingested", "total_bytes_ingested", "total_traces_ingested")
CONSTRAINT_NAME = "uq_billing_meters_tenant_date"


def upgrade() -> None:
    bind = op.get_bind()

    # SQLite has one integer type and stores whatever fits, so the widening is
    # a no-op there; issuing it anyway would force a table rebuild for nothing.
    if bind.dialect.name != "sqlite":
        for column in COUNTERS:
            op.alter_column(
                "billing_meters", column,
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=True,
            )

    bind.execute(
        sa.text(
            "DELETE FROM billing_meters WHERE id NOT IN ("
            "  SELECT MAX(id) FROM billing_meters GROUP BY tenant_id, date"
            ")"
        )
    )

    with op.batch_alter_table("billing_meters") as batch_op:
        batch_op.create_unique_constraint(CONSTRAINT_NAME, ["tenant_id", "date"])


def downgrade() -> None:
    with op.batch_alter_table("billing_meters") as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")

    # Narrowing back would fail on any row that is the reason for this
    # migration, so it is deliberately not attempted.
