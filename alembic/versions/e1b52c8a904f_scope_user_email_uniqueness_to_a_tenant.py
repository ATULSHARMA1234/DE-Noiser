"""an email identifies a user within one organisation, not across the deployment

`users.email` was globally unique. On a single-customer deployment that reads as
"an address is a person". On a multi-tenant one it means the first organisation
to employ someone owns their address everywhere: a second customer's admin
creating an account for the same consultant got "User with this email already
exists" for a person who had no account with them, and no way to proceed. The
same wall stood in front of SCIM provisioning and of onboarding a tenant whose
first admin already worked somewhere else on the deployment.

The constraint becomes `(tenant_id, email)`. Rows with no organisation — the
seeded `admin@` and `system-audit@` accounts on installs old enough to predate
tenancy — keep the old global guarantee through a partial unique index, because
SQL treats NULLs as distinct and the composite constraint would let two of them
collide.

This migration cannot fail on existing data: every row satisfying a global
unique constraint already satisfies a per-tenant one. It only widens what is
permitted from here.

What it does *not* do is decide how a user says which organisation they are
signing in to. That lives in the application: the login route accepts an
optional organisation name and only needs it when one address plus one password
matches in more than one place.

Revision ID: e1b52c8a904f
Revises: d5f81b30e7a2
Create Date: 2026-08-06
"""

import sqlalchemy as sa
from alembic import op

revision = "e1b52c8a904f"
down_revision = "d5f81b30e7a2"
branch_labels = None
depends_on = None


COMPOSITE = "uq_users_tenant_email"
UNASSIGNED = "uq_users_email_unassigned"


def _has_index(bind, name: str) -> bool:
    return any(ix["name"] == name for ix in sa.inspect(bind).get_indexes("users"))


def upgrade() -> None:
    bind = op.get_bind()

    # `ix_users_email` was created unique by the baseline. Dropping and
    # recreating it non-unique is what removes the global constraint; the index
    # itself still earns its place, since every sign-in looks up by address.
    if _has_index(bind, "ix_users_email"):
        op.drop_index("ix_users_email", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    with op.batch_alter_table("users") as batch_op:
        batch_op.create_unique_constraint(COMPOSITE, ["tenant_id", "email"])

    op.create_index(
        UNASSIGNED,
        "users",
        ["email"],
        unique=True,
        sqlite_where=sa.text("tenant_id IS NULL"),
        postgresql_where=sa.text("tenant_id IS NULL"),
    )


def downgrade() -> None:
    # Reversible only while no two organisations share an address — which is the
    # entire point of the upgrade, so this will refuse on any database that has
    # used what it granted. That is the honest failure: silently deleting one of
    # the two accounts to fit the old constraint would destroy a real user.
    bind = op.get_bind()
    clash = bind.execute(
        sa.text(
            "SELECT email FROM users GROUP BY email HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if clash:
        raise RuntimeError(
            f"Cannot restore the global unique constraint: '{clash[0]}' is in use "
            "by more than one organisation. Merge or remove those accounts first."
        )

    op.drop_index(UNASSIGNED, table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint(COMPOSITE, type_="unique")

    op.drop_index("ix_users_email", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=True)
