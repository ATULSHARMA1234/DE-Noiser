"""add per-organisation identity providers

OIDC and SAML settings were read from deployment-wide environment variables, so
one deployment offered one identity provider. Domain routing decided which
organisation a federated identity landed in; it did not let two companies each
bring their own Okta for interactive sign-in. SCIM provisioning was already
per-organisation.

A row per (tenant, protocol), so one organisation can run SAML while another
runs OIDC. The client secret and the IdP certificate are encrypted at rest with
the same key as the SCIM token.

`saml_idp_entity_id` is indexed because it is the inbound routing key: an
assertion names its own issuer inside the signature, which is the only signal
the presenter cannot choose.

Revision ID: c9f207b3e814
Revises: b8e4f1a70c93
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

revision = "c9f207b3e814"
down_revision = "b8e4f1a70c93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_identity_providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("oidc_issuer", sa.String(), nullable=True),
        sa.Column("oidc_client_id", sa.String(), nullable=True),
        sa.Column("oidc_client_secret", sa.String(), nullable=True),
        sa.Column("saml_idp_entity_id", sa.String(), nullable=True),
        sa.Column("saml_idp_sso_url", sa.String(), nullable=True),
        sa.Column("saml_idp_certificate", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tenant_identity_providers_id"),
        "tenant_identity_providers",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tenant_identity_providers_tenant_id"),
        "tenant_identity_providers",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tenant_identity_providers_protocol"),
        "tenant_identity_providers",
        ["protocol"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tenant_identity_providers_saml_idp_entity_id"),
        "tenant_identity_providers",
        ["saml_idp_entity_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_tenant_identity_providers_saml_idp_entity_id"),
        table_name="tenant_identity_providers",
    )
    op.drop_index(
        op.f("ix_tenant_identity_providers_protocol"),
        table_name="tenant_identity_providers",
    )
    op.drop_index(
        op.f("ix_tenant_identity_providers_tenant_id"),
        table_name="tenant_identity_providers",
    )
    op.drop_index(
        op.f("ix_tenant_identity_providers_id"), table_name="tenant_identity_providers"
    )
    op.drop_table("tenant_identity_providers")
