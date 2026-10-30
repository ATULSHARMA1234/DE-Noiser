"""remove spans that belong to no tenant

`/v1/traces` built its `Span` rows without setting `tenant_id`. The column is
nullable, so nothing complained, and every span ingested over OTLP landed owned
by nobody. The consequences were quiet and all in the wrong direction: metering
counts traces per tenant and therefore counted zero; tenant-scoped reads resolve
a NULL owner to the *unassigned* bucket, so an account with no workspace could
see them all; and offboarding deletes by `tenant_id`, so they survived the
erasure of the customer that produced them.

The write path is fixed (see `denoiser.api.otlp`). This clears what it left.

Deletion rather than repair: the owner is not recoverable from the row. The
service name and trace id belong to the customer's systems, not ours, and
guessing an owner for billing and access-control data is worse than having none.
The same spans are still in ClickHouse, which was scoped correctly throughout —
this table is the mirror, not the record.
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c3f5a91e42"
down_revision = "a4e17c92b8d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    orphaned = bind.execute(
        sa.text("SELECT count(*) FROM spans WHERE tenant_id IS NULL")
    ).scalar_one()

    if orphaned:
        # Logged through the migration output rather than silently, so an
        # operator running this against a real deployment sees the size of what
        # it removed and can reconcile it against ClickHouse if they want to.
        print(f"Removing {orphaned} span(s) with no owner")  # noqa: T201
        bind.execute(sa.text("DELETE FROM spans WHERE tenant_id IS NULL"))


def downgrade() -> None:
    # Nothing to restore: the rows carried no owner, which is the whole reason
    # they were removed.
    pass
