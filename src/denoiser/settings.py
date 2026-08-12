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
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
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
    # Comma-separated, most recent first. Retired keys verify but never sign, so
    # a rotation drains outstanding tokens instead of signing every user out.
    # Drop a key from this list once the refresh-token lifetime has elapsed.
    jwt_secret_key_previous: str | None = Field(
        default=None, validation_alias="JWT_SECRET_KEY_PREVIOUS"
    )
    ingest_api_key: str | None = Field(default=None, validation_alias="INGEST_API_KEY")
    admin_password: str | None = Field(default=None, validation_alias="SEMANTICOS_ADMIN_PASSWORD")
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias="CORS_ALLOWED_ORIGINS",
    )
    sso_allow_mock: bool = Field(default=False, validation_alias="SSO_ALLOW_MOCK")
    # Local email+password login. Tri-state: an explicit true/false always wins;
    # left unset it is ON in development and OFF in production. Rationale: MFA is
    # enforced by the enterprise IdP through SSO, and a local password bypasses
    # it. Production operators who need a break-glass admin can re-enable it
    # explicitly with SEMANTICOS_ALLOW_LOCAL_LOGIN=true.
    allow_local_login: bool | None = Field(
        default=None, validation_alias="SEMANTICOS_ALLOW_LOCAL_LOGIN"
    )

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

    @model_validator(mode="before")
    @classmethod
    def _load_file_backed_secrets(cls, values: Any) -> Any:
        """Let any setting be supplied as ``<VAR>_FILE`` pointing at a mounted file.

        This is the shape every secret manager already speaks — Kubernetes
        Secret projections, Vault Agent, the AWS/GCP secret CSI drivers — and
        unlike an environment variable a mounted file can be rotated under a
        running process. An explicit value always wins over the file.
        """
        if not isinstance(values, dict):
            return values
        for name, field in cls.model_fields.items():
            alias = field.validation_alias or name.upper()
            if not isinstance(alias, str):
                continue
            if values.get(name) is not None or os.getenv(alias):
                continue
            path = os.getenv(f"{alias}_FILE")
            if not path:
                continue
            try:
                content = Path(path).read_text(encoding="utf-8").strip()
            except OSError:
                # A missing/unreadable secret file must not crash startup here;
                # the field simply stays unset and validate_for_production
                # reports it as the misconfiguration it is.
                continue
            if content:
                values[name] = content
        return values

    @property
    def jwt_retired_keys(self) -> list[str]:
        raw = self.jwt_secret_key_previous or ""
        return [k.strip() for k in raw.split(",") if k.strip()]

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
    def local_login_enabled(self) -> bool:
        """Whether local email+password login is accepted.

        Explicit override wins; otherwise on in dev, off in production (SSO +
        IdP-enforced MFA only).
        """
        if self.allow_local_login is not None:
            return self.allow_local_login
        return not self.is_production

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

    retired = settings.jwt_retired_keys
    if settings.jwt_secret_key and settings.jwt_secret_key in retired:
        problems.append(
            "JWT_SECRET_KEY also appears in JWT_SECRET_KEY_PREVIOUS — the rotation never took effect"
        )
    if KNOWN_INSECURE_JWT_SECRET in retired:
        problems.append(
            "JWT_SECRET_KEY_PREVIOUS contains the publicly known development default — "
            "tokens forged with it are still accepted"
        )

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

    problems.extend(_external_llm_problems())

    return problems


#: Hosts that are, by definition, inside the operator's own infrastructure.
_LOCAL_LLM_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal")


def _external_llm_problems() -> list[str]:
    """Refuse to send customer log content off-box without an explicit opt-in.

    The product's first claim is that data never leaves the operator's
    infrastructure, and the incident narrator is the one component that can
    break it silently: point ``SLD_LLM_BASE_URL`` at a hosted API and every
    analysed run sends representative log lines to a third party. Nothing said
    so — not a log line, not a startup check — and the README promises the
    opposite.

    This is not a refusal to use a hosted model. It is a refusal to do it by
    accident: set ``LLM_ALLOW_EXTERNAL=true`` and the deployment proceeds, with
    the operator having stated that they know the model is remote and that the
    provider is a data processor they are willing to name in their DPA.

    Read from the environment rather than through ``denoiser.config`` to keep
    this module free of that import; the two settings objects are separate on
    purpose.
    """
    import os
    from urllib.parse import urlsplit

    if os.getenv("LLM_ALLOW_EXTERNAL", "").lower() in ("1", "true", "yes"):
        return []

    enabled = os.getenv("SLD_LLM_ENABLED", "").lower() in ("1", "true", "yes")
    base_url = (os.getenv("SLD_LLM_BASE_URL") or "").strip()
    if not enabled or not base_url:
        return []

    host = (urlsplit(base_url).hostname or "").lower()
    if not host or host in _LOCAL_LLM_HOSTS:
        return []
    # A name with no dot cannot be a public DNS name. It is a Compose service, a
    # Kubernetes Service, or a hosts-file entry — all of them the operator's own
    # network. `http://ollama:11434` is the documented local setup.
    if "." not in host:
        return []
    # Cluster-internal and link-local suffixes, likewise.
    if host.endswith((".local", ".internal", ".svc", ".svc.cluster.local")):
        return []
    # A private address is still the operator's own network.
    try:
        import ipaddress

        if ipaddress.ip_address(host).is_private:
            return []
    except ValueError:
        pass  # not a literal address; fall through to the check below

    return [
        f"SLD_LLM_BASE_URL points at {host}, which is outside this deployment — "
        "analysed log content would be sent to a third party, contradicting the "
        "privacy claim this product is sold on. Use a local model, or set "
        "LLM_ALLOW_EXTERNAL=true to accept it deliberately and name the provider "
        "as a processor in your DPA."
    ]


def load_dotenv_into_environ(path: str | os.PathLike[str] = ".env") -> int:
    """Export ``.env`` into ``os.environ``. Returns how many keys were set.

    ``InfraSettings`` parses ``.env`` itself, but plenty of infrastructure
    clients (the ClickHouse store, the Redis and Kafka clients, the database
    URL) still read ``os.getenv`` directly. Under docker-compose those come from
    real environment variables so everything agrees; running the API straight
    from a checkout, they did not — the app would authenticate to ClickHouse
    with an empty password while ``.env`` held the real one, and report the
    server as unavailable. Exporting once at import makes the two paths agree.

    Real environment variables always win, so containers and CI are unaffected.
    Skipped under pytest so the suite never inherits a developer's local ``.env``.
    """
    if is_testing():
        return 0

    env_path = Path(path)
    if not env_path.is_file():
        return 0

    exported = 0
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.removeprefix("export ").partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value or key in os.environ:
            continue
        os.environ[key] = value
        exported += 1
    return exported


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
