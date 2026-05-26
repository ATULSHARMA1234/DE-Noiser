"""Initial schema creation for SemanticOS.

Creates:
- incidents
- analysis_runs

This is a hand-written equivalent of what `alembic --autogenerate` would
produce from the current SQLAlchemy models in `denoiser.storage.db`.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision: str = "000000000001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("status", sa.String(), server_default="OPEN", nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("domain", sa.String(), nullable=True),
        sa.Column("impact_score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("remediation_hints", sa.JSON(), nullable=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("total_logs", sa.Integer(), nullable=True),
        sa.Column("cluster_count", sa.Integer(), nullable=True),
    )

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("raw_lines", sa.Integer(), nullable=True),
        sa.Column("cluster_count", sa.Integer(), nullable=True),
        sa.Column("reduction_ratio", sa.Float(), nullable=True),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("clusters_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("analysis_runs")
    op.drop_table("incidents")

