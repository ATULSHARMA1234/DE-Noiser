"""add log issues, comments and activity

A cluster only existed inside the run that produced it — HDBSCAN renumbers
cluster ids on every run — so the same failing pattern was reported as brand new
each time and could carry no history, no triage state and no owner. These tables
hold the durable identity for a pattern: fingerprint, first/last seen, a merged
occurrence histogram, tag prevalence, samples, plus the comments and activity a
team triages from.

Revision ID: c8e2a4f70b19
Revises: b1c7f92e4d38
Create Date: 2026-07-26 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c8e2a4f70b19'
down_revision: Union[str, None] = 'b1c7f92e4d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'log_issues',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('fingerprint', sa.String(), nullable=False),
        sa.Column('template_hashes', sa.JSON(), nullable=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('template', sa.String(), nullable=True),
        sa.Column('representative_log', sa.String(), nullable=True),
        sa.Column('service', sa.String(), nullable=True),
        sa.Column('severity', sa.String(), nullable=True),
        sa.Column('state', sa.String(), nullable=True),
        sa.Column('assignee_id', sa.Integer(), nullable=True),
        sa.Column('team_id', sa.Integer(), nullable=True),
        sa.Column('first_seen', sa.DateTime(), nullable=True),
        sa.Column('last_seen', sa.DateTime(), nullable=True),
        sa.Column('total_events', sa.Integer(), nullable=True),
        sa.Column('run_count', sa.Integer(), nullable=True),
        sa.Column('last_run_id', sa.String(), nullable=True),
        sa.Column('last_cluster_id', sa.Integer(), nullable=True),
        sa.Column('anomaly_score', sa.Float(), nullable=True),
        sa.Column('is_noise', sa.Boolean(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('histogram', sa.JSON(), nullable=True),
        sa.Column('samples', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_log_issues_id'), 'log_issues', ['id'], unique=False)
    op.create_index(op.f('ix_log_issues_tenant_id'), 'log_issues', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_log_issues_fingerprint'), 'log_issues', ['fingerprint'], unique=False)
    op.create_index(op.f('ix_log_issues_service'), 'log_issues', ['service'], unique=False)
    op.create_index(op.f('ix_log_issues_severity'), 'log_issues', ['severity'], unique=False)
    op.create_index(op.f('ix_log_issues_state'), 'log_issues', ['state'], unique=False)
    op.create_index(op.f('ix_log_issues_assignee_id'), 'log_issues', ['assignee_id'], unique=False)
    op.create_index(op.f('ix_log_issues_team_id'), 'log_issues', ['team_id'], unique=False)
    op.create_index(op.f('ix_log_issues_first_seen'), 'log_issues', ['first_seen'], unique=False)
    op.create_index(op.f('ix_log_issues_last_seen'), 'log_issues', ['last_seen'], unique=False)

    op.create_table(
        'issue_comments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('issue_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('author_email', sa.String(), nullable=True),
        sa.Column('body', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_issue_comments_id'), 'issue_comments', ['id'], unique=False)
    op.create_index(op.f('ix_issue_comments_tenant_id'), 'issue_comments', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_issue_comments_issue_id'), 'issue_comments', ['issue_id'], unique=False)

    op.create_table(
        'issue_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('issue_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('actor_email', sa.String(), nullable=True),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_issue_events_id'), 'issue_events', ['id'], unique=False)
    op.create_index(op.f('ix_issue_events_tenant_id'), 'issue_events', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_issue_events_issue_id'), 'issue_events', ['issue_id'], unique=False)
    op.create_index(op.f('ix_issue_events_created_at'), 'issue_events', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_table('issue_events')
    op.drop_table('issue_comments')
    op.drop_table('log_issues')
