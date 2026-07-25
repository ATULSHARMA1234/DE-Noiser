"""add slo latency threshold

A latency SLI was measured against a 500ms constant buried in the engine, so
every latency SLO in the system shared one objective nobody had agreed to.
The threshold belongs to the SLO.

Revision ID: a9d3e6b17c40
Revises: f4b7c1e9d206
Create Date: 2026-07-26 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a9d3e6b17c40'
down_revision: Union[str, None] = 'f4b7c1e9d206'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('slos') as batch_op:
        batch_op.add_column(
            sa.Column('latency_threshold_ms', sa.Float(), nullable=True)
        )
    # Existing latency SLOs keep the behaviour they were created under rather
    # than silently changing objective on upgrade.
    op.execute(
        "UPDATE slos SET latency_threshold_ms = 500.0 WHERE latency_threshold_ms IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table('slos') as batch_op:
        batch_op.drop_column('latency_threshold_ms')
