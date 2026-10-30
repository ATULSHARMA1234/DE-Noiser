"""add tenant api key rotation

The tenant API key is the credential customers paste into log shippers, and
there was no way to change it: no endpoint, no overlap, nothing. A leaked key
stayed valid forever. These columns hold the superseded key and the window it
remains accepted for, so a rotation can be rolled out agent by agent.

Revision ID: f4b7c1e9d206
Revises: e3c8f5a2b91d
Create Date: 2026-07-26 04:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f4b7c1e9d206'
down_revision: Union[str, None] = 'e3c8f5a2b91d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenants') as batch_op:
        batch_op.add_column(sa.Column('api_key_previous', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('api_key_previous_expires_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('api_key_rotated_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_tenants_api_key_previous'), 'tenants', ['api_key_previous'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tenants_api_key_previous'), table_name='tenants')
    with op.batch_alter_table('tenants') as batch_op:
        batch_op.drop_column('api_key_rotated_at')
        batch_op.drop_column('api_key_previous_expires_at')
        batch_op.drop_column('api_key_previous')
