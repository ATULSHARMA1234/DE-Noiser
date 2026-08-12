"""add platform settings

Operator settings lived in data/settings.json on the API pod's own filesystem,
which made the API stateful: a second replica could not see what the first one
saved, and both could clobber each other. The database is already shared and
already backed up.

Revision ID: b1c7f92e4d38
Revises: a9d3e6b17c40
Create Date: 2026-07-26 12:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b1c7f92e4d38'
down_revision: Union[str, None] = 'a9d3e6b17c40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_platform_settings_id'), 'platform_settings', ['id'], unique=False)
    # The existing settings.json is imported on first read (see
    # denoiser.api.platform_settings), so no data move is needed here.


def downgrade() -> None:
    op.drop_index(op.f('ix_platform_settings_id'), table_name='platform_settings')
    op.drop_table('platform_settings')
