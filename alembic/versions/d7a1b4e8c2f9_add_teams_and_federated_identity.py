"""add teams and federated identity

Adds the ``teams`` table and the ``users.external_id`` / ``users.teams`` columns
that back SSO (OIDC) login and SCIM provisioning.

Revision ID: d7a1b4e8c2f9
Revises: c5d9e2f1a3b7
Create Date: 2026-07-25 02:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7a1b4e8c2f9'
down_revision: Union[str, None] = 'c5d9e2f1a3b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'teams',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('external_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_teams_id'), 'teams', ['id'], unique=False)
    op.create_index(op.f('ix_teams_tenant_id'), 'teams', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_teams_external_id'), 'teams', ['external_id'], unique=False)

    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('external_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('teams', sa.JSON(), nullable=True))
    op.create_index(op.f('ix_users_external_id'), 'users', ['external_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_external_id'), table_name='users')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('teams')
        batch_op.drop_column('external_id')
    op.drop_index(op.f('ix_teams_external_id'), table_name='teams')
    op.drop_index(op.f('ix_teams_tenant_id'), table_name='teams')
    op.drop_index(op.f('ix_teams_id'), table_name='teams')
    op.drop_table('teams')
