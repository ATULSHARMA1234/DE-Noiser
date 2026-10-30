"""add tenant settings

Platform settings were one row shared by every tenant, so one organisation's
ADMIN set retention and PII redaction for all of them. Each tenant's own
settings get a row here. No data is moved: a tenant without a row inherits the
deployment row's values until it saves its own.

Revision ID: a9c4e2f7b310
Revises: e1b52c8a904f
Create Date: 2026-10-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a9c4e2f7b310'
down_revision: Union[str, None] = 'e1b52c8a904f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenant_settings',
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('tenant_id'),
    )


def downgrade() -> None:
    op.drop_table('tenant_settings')
