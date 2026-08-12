"""give saved queries an owner

`saved_queries` was the one mapped table with no `tenant_id`. `GET /query/saved`
listed every organisation's saved queries to every caller, and
`DELETE /query/saved/{id}` deleted any of them by id — scoped by neither tenant
nor owner.

Existing rows are adopted from the user that created them, which is the owner
the route already recorded. A row whose `user_id` is NULL, or points at a user
that no longer exists, has no recoverable owner and stays NULL — the unassigned
bucket, which is where an unassigned caller looks for it.

Revision ID: a1d5c83f206e
Revises: f7a2c04b91de
Create Date: 2026-07-28 16:05:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1d5c83f206e'
down_revision: Union[str, None] = 'f7a2c04b91de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("saved_queries", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.create_index("ix_saved_queries_tenant_id", "saved_queries", ["tenant_id"])
    op.get_bind().execute(
        sa.text(
            "UPDATE saved_queries SET tenant_id = ("
            "  SELECT users.tenant_id FROM users WHERE users.id = saved_queries.user_id"
            ") WHERE user_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_saved_queries_tenant_id", table_name="saved_queries")
    op.drop_column("saved_queries", "tenant_id")
