"""add monitor evaluation state

Monitors stored a query and thresholds but nothing ever ran them, so there was
nowhere to record the result. These columns hold the evaluator's output: the
window it aggregates over, the last observed value, the resulting status, and
when it last ran or fired.

Revision ID: e3c8f5a2b91d
Revises: d7a1b4e8c2f9
Create Date: 2026-07-26 03:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e3c8f5a2b91d'
down_revision: Union[str, None] = 'd7a1b4e8c2f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('monitors') as batch_op:
        batch_op.add_column(sa.Column('window_seconds', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('status', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('last_value', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('last_evaluated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('last_triggered_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('last_error', sa.String(), nullable=True))

    # Existing rows predate evaluation: give them the defaults the model uses so
    # the UI does not have to special-case NULL.
    op.execute("UPDATE monitors SET window_seconds = 300 WHERE window_seconds IS NULL")
    op.execute("UPDATE monitors SET status = 'PENDING' WHERE status IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('monitors') as batch_op:
        batch_op.drop_column('last_error')
        batch_op.drop_column('last_triggered_at')
        batch_op.drop_column('last_evaluated_at')
        batch_op.drop_column('last_value')
        batch_op.drop_column('status')
        batch_op.drop_column('window_seconds')
