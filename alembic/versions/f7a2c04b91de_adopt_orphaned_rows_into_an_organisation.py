"""adopt rows that predate tenant scoping into the first organisation

Notebooks, metric rules and extracted metrics created before `tenant_id` existed
carry NULL, and three routes read that as "shared": they matched
``tenant_id == me OR tenant_id IS NULL`` so an upgraded deployment would not
appear to lose its data.

That is correct for one customer and wrong for two. A shared row is readable —
and, for notebooks and metric rules, *writable* — by every organisation on the
deployment, so one customer can read and edit another's legacy work.

The ambiguity belongs in the data, not in the predicate. Orphaned rows are
adopted by the first organisation (the one that existed when they were written),
after which ownership is a plain equality everywhere and no route needs a
special case. A deployment with no organisations at all has nothing to adopt
into and is left alone.

Revision ID: f7a2c04b91de
Revises: e6b3d80f5a24
Create Date: 2026-07-27 21:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f7a2c04b91de'
down_revision: Union[str, None] = 'e6b3d80f5a24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Only the tables whose routes actually treated NULL as shared. Other tables
#: carry NULL rows too, but nothing ever showed them across a tenant boundary,
#: so adopting them would change ownership without fixing anything.
ADOPTED_TABLES = ("notebooks", "metric_rules", "extracted_metrics")


def upgrade() -> None:
    bind = op.get_bind()
    first = bind.execute(sa.text("SELECT id FROM tenants ORDER BY id LIMIT 1")).scalar()
    if first is None:
        # No organisations exist yet, so there is no owner to adopt into. The
        # rows stay NULL and belong to the unassigned bucket, which is exactly
        # where an unassigned caller looks for them.
        return

    for table in ADOPTED_TABLES:
        bind.execute(
            sa.text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"),
            {"tid": first},
        )


def downgrade() -> None:
    # Deliberately empty. The adopted rows are indistinguishable from rows that
    # legitimately belong to the first organisation, so returning them to NULL
    # would orphan real data to undo a data repair.
    pass
