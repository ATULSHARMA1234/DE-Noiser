"""add incident severity

Adds the ``severity`` priority-label column to ``incidents``. The automation
engine and runbook trigger matching read it, and every incident writer now sets
it — before this the column did not exist and those code paths raised
AttributeError at runtime.

Revision ID: b2f4a1c9d7e3
Revises: 773789731d39
Create Date: 2026-07-25 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b2f4a1c9d7e3'
down_revision: Union[str, None] = '773789731d39'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: existing rows have no severity and there is no safe backfill
    # value; new rows are written with one.
    with op.batch_alter_table('incidents') as batch_op:
        batch_op.add_column(sa.Column('severity', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('incidents') as batch_op:
        batch_op.drop_column('severity')
