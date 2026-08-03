"""add erasure records

Persists the outcome of a tenant offboarding so an erasure can be certified
against the ClickHouse mutations actually finishing, rather than against the
response of the endpoint that submitted them. ClickHouse `ALTER … DELETE` is
asynchronous: the old response meant "accepted", and on a large table that is
minutes or hours before the data is gone.

The table is deliberately not tenant-scoped. Everything belonging to the
customer is deleted by the purge, so a tenant-scoped record would be destroyed
by the operation it exists to prove.

Revision ID: b8e4f1a70c93
Revises: a1d5c83f206e
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

revision = "b8e4f1a70c93"
down_revision = "a1d5c83f206e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erasure_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purged_tenant_id", sa.Integer(), nullable=False),
        sa.Column("tenant_name", sa.String(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("clickhouse_mutations", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("requested_by", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_erasure_records_id"), "erasure_records", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_erasure_records_purged_tenant_id"),
        "erasure_records",
        ["purged_tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_erasure_records_purged_tenant_id"), table_name="erasure_records")
    op.drop_index(op.f("ix_erasure_records_id"), table_name="erasure_records")
    op.drop_table("erasure_records")
