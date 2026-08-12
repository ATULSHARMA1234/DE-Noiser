"""add webhooks table and tenant-scope the audit and alert logs

Alert destinations lived in a process-global dict inside the AlertRouter. That
had two consequences: every destination was lost on restart, and no route could
distinguish one tenant's destinations from another's — any tenant admin could
list, modify, fire and delete every other tenant's alert routing, and read the
webhook URLs, which are bearer credentials.

The audit and alert logs had the same shape of problem for the same reason: no
tenant column, so a single global stream was served to whoever asked.

Revision ID: d4f1a7c93e52
Revises: c8e2a4f70b19
Create Date: 2026-07-26 19:10:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4f1a7c93e52'
down_revision: Union[str, None] = 'c8e2a4f70b19'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'webhooks',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('channel_type', sa.String(), nullable=False),
        # Encrypted at rest: a Slack/PagerDuty URL authenticates by possession,
        # so a database dump would otherwise hand over live credentials.
        sa.Column('url_encrypted', sa.String(), nullable=False),
        sa.Column('min_priority', sa.String(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_webhooks_id'), 'webhooks', ['id'], unique=False)
    op.create_index(op.f('ix_webhooks_tenant_id'), 'webhooks', ['tenant_id'], unique=False)

    with op.batch_alter_table('audit_logs') as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_audit_logs_tenant_id'), 'audit_logs', ['tenant_id'], unique=False)

    with op.batch_alter_table('alert_logs') as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_alert_logs_tenant_id'), 'alert_logs', ['tenant_id'], unique=False)

    # Backfill: attribute existing audit rows to the tenant of the user that
    # produced them. Rows with no resolvable user stay NULL and are visible only
    # to the tenant they cannot be attributed to — i.e. nobody — which is the
    # safe default for a record whose owner is unknown.
    op.execute(
        "UPDATE audit_logs SET tenant_id = ("
        "  SELECT users.tenant_id FROM users WHERE users.id = audit_logs.user_id"
        ") WHERE user_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_alert_logs_tenant_id'), table_name='alert_logs')
    with op.batch_alter_table('alert_logs') as batch_op:
        batch_op.drop_column('tenant_id')

    op.drop_index(op.f('ix_audit_logs_tenant_id'), table_name='audit_logs')
    with op.batch_alter_table('audit_logs') as batch_op:
        batch_op.drop_column('tenant_id')

    op.drop_index(op.f('ix_webhooks_tenant_id'), table_name='webhooks')
    op.drop_index(op.f('ix_webhooks_id'), table_name='webhooks')
    op.drop_table('webhooks')
