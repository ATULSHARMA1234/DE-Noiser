"""deduplicate spans and constrain their identity

A span is identified by its own id within its trace, within one customer, and
nothing enforced that. The trace ingest endpoint committed its Postgres rows
before it knew whether the trace store had accepted the batch, so a ClickHouse
outage produced a 503, the exporter retried — roughly six times over a five
minute backoff, with default OTLP settings — and each attempt committed another
full copy of every span.

The write path is fixed (see `denoiser.api.otlp`), but the rows that fix leaves
behind are already in the table, and an idempotent insert needs a constraint to
be idempotent against. This removes the duplicates and adds the constraint.

The lowest `id` in each group is kept: it is the copy that was written first, so
retained rows keep the order they arrived in.

Uniqueness is expressed as an index over `coalesce(tenant_id, -1)` rather than a
constraint on the bare column, because both PostgreSQL and SQLite treat NULLs as
distinct — and at the time this was written *every* span had a NULL owner, so a
plain constraint would have enforced nothing at all. The ingest path is fixed
separately; the guarantee should not depend on that having happened.

Revision ID: a4e17c92b8d1
Revises: d0a6b41e73c5
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "a4e17c92b8d1"
down_revision = "d0a6b41e73c5"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_spans_identity"


def upgrade() -> None:
    bind = op.get_bind()

    # GROUP BY treats NULLs as equal on both PostgreSQL and SQLite, so this
    # collapses unattributed duplicates too. `id` is the primary key, so MIN(id)
    # is never NULL and the NOT IN is safe.
    bind.execute(
        sa.text(
            "DELETE FROM spans WHERE id NOT IN ("
            "  SELECT MIN(id) FROM spans GROUP BY tenant_id, trace_id, span_id"
            ")"
        )
    )

    # An expression index, so it is created with raw SQL rather than
    # `op.create_index` — the expression is the point, and both engines accept
    # this spelling. No table rewrite on either, so no batch_alter_table.
    bind.execute(
        sa.text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} "
            "ON spans (coalesce(tenant_id, -1), trace_id, span_id)"
        )
    )


def downgrade() -> None:
    # The deleted duplicates are not recoverable, and were never wanted. Only
    # the index comes off.
    op.get_bind().execute(sa.text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
