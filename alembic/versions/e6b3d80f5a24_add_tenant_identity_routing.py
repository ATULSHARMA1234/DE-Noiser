"""route federated identities to an organisation by email domain

SSO and SCIM both attributed every federated user to whichever tenant had the
lowest id. That is correct for a deployment serving one customer and wrong for
one serving several: an employee of the second company signing in through their
own IdP landed inside the first company's data.

`sso_domains` records the email domains a customer owns, so an identity can be
routed to its real organisation, and `scim_token` gives each customer their own
provisioning credential, so one company's IdP cannot manage another's staff.

Revision ID: e6b3d80f5a24
Revises: d4f1a7c93e52
Create Date: 2026-07-27 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e6b3d80f5a24'
down_revision: Union[str, None] = 'd4f1a7c93e52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table so this also applies on SQLite, which cannot ALTER a
    # column in place.
    with op.batch_alter_table('tenants') as batch:
        batch.add_column(sa.Column('sso_domains', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('scim_token', sa.String(), nullable=True))
        batch.add_column(sa.Column('scim_token_rotated_at', sa.DateTime(), nullable=True))

    # Left NULL rather than defaulted to an empty list, because "no domains
    # registered anywhere" is the signal that keeps a single-customer
    # deployment on its existing behaviour instead of failing every SSO login.


def downgrade() -> None:
    with op.batch_alter_table('tenants') as batch:
        batch.drop_column('scim_token_rotated_at')
        batch.drop_column('scim_token')
        batch.drop_column('sso_domains')
