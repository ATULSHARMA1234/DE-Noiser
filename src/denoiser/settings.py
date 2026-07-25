"""
Infrastructure configuration.

``denoiser.config.DenoiserConfig`` covers the analysis pipeline (SLD_* prefixed:
model names, thresholds, clustering). This module covers the *deployment* — the
addresses, credentials and switches that decide whether the process can talk to
anything. They were previously read with bare ``os.getenv`` at 21 call sites
scattered across the codebase, which has two consequences worth fixing:

  - a missing or misspelled variable surfaces as a 500 on whichever request
    first touches that subsystem, possibly hours after the deploy, rather than
    at startup;
  - there is nowhere to state that a setting is mandatory in production and
    optional in development, so unsafe defaults survive into real deployments.

Everything is read once, validated together, and ``validate_for_production``
turns the second class of problem into a refusal to boot.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Shipped in an old commit as a fallback. If it ever appears in a real
# deployment, every token the platform has issued is forgeable.
KNOWN_INSECURE_JWT_SECRET = "semantic-os-super-secure-production-secret-key-1234567890"


class InfraSettings(BaseSettings):
    """Deployment configuration, read from the environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Each field declares the bare environment variable it reads via
        # validation_alias. Without this, that alias becomes the *only* accepted
        # key and the field name stops working for direct construction, which
        # every caller and test uses.
        populate_by_name=True,
    )

    # ── Environment ──────────────────────────────────────────────────────
    environment: str = Field(
        default="development",
        validation_alias="SEMANTICOS_ENV",
        description="'production' enables the strict startup checks.",
    )

    # ── Datastores ───────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite:///./data/semantic_os.db",
        validation_alias="DATABASE_URL",
    )
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")
    kafka_broker: str | None = Field(default=None, validation_alias="KAFKA_BROKER")

    clickhouse_host: str = Field(default="localhost", validation_alias="CLICKHOUSE_HOST")
    clickhouse_port: int = Field(default=8123, validation_alias="CLICKHOUSE_PORT")
    clickhouse_user: str = Field(default="default", validation_alias="CLICKHOUSE_USER")
    clickhouse_password: str | None = Field(default=None, validation_alias="CLICKHOUSE_PASSWORD")
    clickhouse_db: str = Field(default="default", validation_alias="CLICKHOUSE_DB")

    # ── Security ─────────────────────────────────────────────────────────
    jwt_secret_key: str | None = Field(default=None, validation_alias="JWT_SECRET_KEY")
    ingest_api_key: str | None = Field(default=None, validation_alias="INGEST_API_KEY")
    admin_password: str | None = Field(default=None, validation_alias="SEMANTICOS_ADMIN_PASSWORD")
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias="CORS_ALLOWED_ORIGINS",
    )
    sso_allow_mock: bool = Field(default=False, validation_alias="SSO_ALLOW_MOCK")

    # ── Enterprise identity: OIDC SSO ────────────────────────────────────
    # When issuer + client id/secret are set, the real OIDC Authorization Code
    # flow is used instead of the mock IdP.
    oidc_issuer: str | None = Field(default=None, validation_alias="OIDC_ISSUER")
    oidc_client_id: str | None = Field(default=None, validation_alias="OIDC_CLIENT_ID")
    oidc_client_secret: str | None = Field(default=None, validation_alias="OIDC_CLIENT_SECRET")
    oidc_redirect_uri: str | None = Field(default=None, validation_alias="OIDC_REDIRECT_URI")
    oidc_scopes: str = Field(default="openid email profile groups", validation_alias="OIDC_SCOPES")
    # Group (from the IdP `groups` claim / SCIM) that grants the ADMIN role.
    oidc_admin_group: str = Field(default="semanticos-admins", validation_alias="OIDC_ADMIN_GROUP")
    oidc_analyst_group: str = Field(default="semanticos-analysts", validation_alias="OIDC_ANALYST_GROUP")

    # ── Enterprise identity: SCIM 2.0 provisioning ───────────────────────
    # Bearer token the IdP presents to the SCIM endpoints for automated
    # user/group provisioning and de-provisioning.
    scim_bearer_token: str | None = Field(default=None, validation_alias="SCIM_BEARER_TOKEN")

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)

    # ── Behaviour switches ───────────────────────────────────────────────
    auto_migrate: bool = Field(default=True, validation_alias="SEMANTICOS_AUTO_MIGRATE")
    scheduler_enabled: bool = Field(default=True, validation_alias="SEMANTICOS_SCHEDULER_ENABLED")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


def validate_for_production(settings: InfraSettings) -> list[str]:
    """Return the reasons this configuration is unsafe to serve production with.

    Empty list means safe. Checks only things that are silently dangerous rather
    than merely absent — a missing SMTP host degrades a feature, a forgeable JWT
    secret compromises every account.
    """
    problems: list[str] = []

    if not settings.jwt_secret_key:
        problems.append("JWT_SECRET_KEY is not set — tokens cannot be signed securely")
    elif settings.jwt_secret_key == KNOWN_INSECURE_JWT_SECRET:
        problems.append("JWT_SECRET_KEY is the publicly known development default — every token is forgeable")
    elif len(settings.jwt_secret_key) < 32:
        problems.append(f"JWT_SECRET_KEY is only {len(settings.jwt_secret_key)} characters — use at least 32")

    if "*" in settings.cors_origin_list:
        problems.append("CORS_ALLOWED_ORIGINS contains '*', which is unsafe on a credentialed API")

    if any(o.startswith("http://") and "localhost" not in o and "127.0.0.1" not in o for o in settings.cors_origin_list):
        problems.append("CORS_ALLOWED_ORIGINS contains a plaintext http:// origin — tokens would travel unencrypted")

    if settings.sso_allow_mock:
        problems.append("SSO_ALLOW_MOCK is enabled — the mock IdP issues real platform tokens with no verification")

    if not settings.admin_password:
        problems.append("SEMANTICOS_ADMIN_PASSWORD is not set — the seeded admin account would use a generated password")

    if settings.database_url.startswith("sqlite"):
        problems.append("DATABASE_URL is SQLite — it cannot serve multiple API replicas safely")

    return problems


@lru_cache
def get_settings() -> InfraSettings:
    """Process-wide settings. Cached so the environment is read once."""
    return InfraSettings()


def reload_settings() -> InfraSettings:
    """Drop the cache and re-read. For tests that manipulate the environment."""
    get_settings.cache_clear()
    return get_settings()


def is_testing() -> bool:
    import sys

    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ
