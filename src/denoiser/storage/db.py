"""
Database models and session management for SemanticOS.

Task 5: Supports both SQLite (local dev) and PostgreSQL (production).
The DATABASE_URL is read from environment variables / .env file.
"""

from __future__ import annotations

import os

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from denoiser.utils.time import utcnow

# ── Task 5: Dual-database support ───────────────────────────────────────────
# Default to SQLite for zero-config local development.
# Set DATABASE_URL=postgresql://user:pass@host:5432/semanticos for production.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/semantic_os.db")

# SQLite requires check_same_thread=False; PostgreSQL does not.
_connect_args = {}
_engine_args = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
    _engine_args["poolclass"] = NullPool
else:
    _engine_args["pool_size"] = 20
    _engine_args["max_overflow"] = 50

engine = create_engine(DATABASE_URL, connect_args=_connect_args, **_engine_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# ── Models ───────────────────────────────────────────────────────────────────

class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    status = Column(String, default="OPEN")
    title = Column(String)
    domain = Column(String)
    # Priority label (P0..P3). Runbook matching and the automation engine read
    # this; every writer must set it. Nullable so create_all-era rows adopt.
    severity = Column(String, nullable=True)
    impact_score = Column(Float)
    created_at = Column(DateTime, default=utcnow)
    resolved_at = Column(DateTime, nullable=True)

    # Intelligence
    summary = Column(JSON)  # List of bullet points or string
    remediation_hints = Column(JSON)  # List of hints

    # Linked analysis context
    run_id = Column(String, nullable=True)
    source = Column(String, nullable=True)
    total_logs = Column(Integer, nullable=True)
    cluster_count = Column(Integer, nullable=True)

    # Predictive AI
    is_predictive = Column(Boolean, default=False)
    forecasted_depletion_time = Column(DateTime, nullable=True)


class Team(Base):
    """A team / group within a tenant, provisioned via SCIM Groups or created
    locally. Team membership is mirrored onto ``User.teams`` for fast checks."""
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    name = Column(String, nullable=False)
    # SCIM group id / IdP group id, so the provisioner can match on update.
    external_id = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow)


class RevokedToken(Base):
    """A JWT that has been explicitly invalidated (logout / forced sign-out).

    Keyed by the token's ``jti`` claim. Rows are pruned once ``expires_at`` has
    passed — a revoked token is only interesting until it would expire anyway.
    """
    __tablename__ = "revoked_tokens"

    jti = Column(String, primary_key=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, default=utcnow)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id = Column(String, primary_key=True, index=True)  # e.g. run_a1b2c3d4
    tenant_id = Column(Integer, index=True, nullable=True)
    source = Column(String)
    status = Column(String)
    raw_lines = Column(Integer)
    cluster_count = Column(Integer)
    reduction_ratio = Column(Float)
    duration_sec = Column(Float)
    created_at = Column(DateTime, default=utcnow)
    clusters_snapshot = Column(JSON, nullable=True)


class User(Base):
    __tablename__ = "users"

    # An address identifies a person *within one organisation*, not across the
    # deployment. Globally unique emails meant the first customer to hire a
    # consultant took their address for everyone: the second customer's admin
    # got "User with this email already exists" for a person who had no account
    # with them at all, and no way to create one. Two companies can now each
    # have the same contractor on staff, as separate accounts with separate
    # passwords and separate data.
    #
    # The partial index keeps the old guarantee for rows with no organisation —
    # the seeded platform accounts — where the composite constraint cannot: SQL
    # treats NULLs as distinct, so (NULL, 'a@b') never collides with itself.
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index(
            "uq_users_email_unassigned",
            "email",
            unique=True,
            sqlite_where=text("tenant_id IS NULL"),
            postgresql_where=text("tenant_id IS NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    email = Column(String, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="VIEWER", nullable=False)  # ADMIN, ANALYST, VIEWER
    is_active = Column(Boolean, default=True)
    department = Column(String, default="Engineering", nullable=False)
    environment_access = Column(JSON, default=list)
    # Identity federation: the stable subject/id from the IdP (OIDC `sub` or SCIM
    # user id). Lets SSO/SCIM match an existing user even if their email changes.
    external_id = Column(String, nullable=True, index=True)
    # Team names the user belongs to (populated from SCIM Groups / IdP claims).
    teams = Column(JSON, default=list)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    # Without this the audit trail is a single global stream: one tenant's admin
    # could read every other tenant's actions, user ids, and source IPs.
    tenant_id = Column(Integer, index=True, nullable=True)
    user_id = Column(Integer, nullable=True)  # Nullable for unauthenticated actions
    action = Column(String, nullable=False)
    resource_type = Column(String, nullable=False)
    resource_id = Column(String, nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String, nullable=True)
    timestamp = Column(DateTime, default=utcnow, index=True)

class AlertLog(Base):
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    webhook_id = Column(String, index=True)
    alert_fingerprint = Column(String, index=True)
    priority = Column(String)
    status = Column(String)
    http_status = Column(Integer, nullable=True)
    latency_ms = Column(Float, nullable=True)
    error = Column(String, nullable=True)
    timestamp = Column(String, default=lambda: utcnow().isoformat(), index=True)


class Webhook(Base):
    """A registered alert destination, owned by exactly one tenant.

    Previously these lived in a process-global dict inside the AlertRouter,
    which had two consequences: every destination was lost on restart, and no
    route could tell one tenant's destinations from another's.

    ``url`` holds a credential (a Slack or PagerDuty webhook URL authenticates
    by possession), so it is stored encrypted and never returned in full.
    """
    __tablename__ = "webhooks"

    id = Column(String, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=False)
    name = Column(String, nullable=False)
    channel_type = Column(String, nullable=False)
    url_encrypted = Column(String, nullable=False)
    min_priority = Column(String, default="P1")
    enabled = Column(Boolean, default=True)
    extra = Column(JSON, default=dict)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

# ── Wave 2 Models ────────────────────────────────────────────────────────────

class Span(Base):
    __tablename__ = "spans"

    #: A span is identified by its own id within its trace, within one
    #: customer. Without this, a retried OTLP batch — which is the normal
    #: reaction to a 503, and the normal reaction to a lost response — wrote a
    #: second copy of every span it contained.
    #:
    #: An index over `coalesce(tenant_id, -1)` rather than a plain unique
    #: constraint, because both PostgreSQL and SQLite treat NULLs as distinct:
    #: a constraint on the bare column would not deduplicate unattributed rows,
    #: which is precisely the state every span was in when this was written. The
    #: ingest path now sets an owner and a migration clears the old NULLs, but
    #: the restore path in `storage.archiver` reads its tenant out of an archive
    #: file and can still produce one, so the guarantee should not depend on
    #: that column being populated.
    __table_args__ = (
        Index(
            "uq_spans_identity",
            text("coalesce(tenant_id, -1)"),
            text("trace_id"),
            text("span_id"),
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    trace_id = Column(String, index=True, nullable=False)
    span_id = Column(String, index=True, nullable=False)
    parent_span_id = Column(String, index=True, nullable=True)
    service_name = Column(String, index=True, nullable=False)
    operation_name = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    duration_ms = Column(Float, nullable=False)
    status_code = Column(String, nullable=True)  # e.g., "OK", "ERROR"
    attributes = Column(JSON, nullable=True)
    events = Column(JSON, nullable=True)

class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id = Column(Integer, primary_key=True, index=True)
    # The only model here that used to carry no owner at all: /query/saved
    # listed and deleted across every organisation on the deployment.
    tenant_id = Column(Integer, index=True, nullable=True)
    name = Column(String, nullable=False)
    query_text = Column(String, nullable=False)
    user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    last_used = Column(DateTime, default=utcnow)

class TenantIdentityProvider(Base):
    """One organisation's own IdP.

    OIDC and SAML settings were read from deployment-wide environment
    variables, so a shared deployment offered exactly one identity provider.
    Domain routing decided *which organisation* an assertion landed in; it did
    not let two companies each bring their own Okta for interactive login,
    which is table stakes for hosting more than one customer. SCIM was already
    per-organisation and did not have this limitation.

    A row per (tenant, protocol), so one organisation can run SAML while
    another runs OIDC, and a single organisation can offer both.

    The client secret and the IdP certificate are stored through the same
    encryption used for the SCIM token. The certificate is a public key and not
    strictly a secret, but it is the root of trust for every assertion that
    organisation presents — anyone who can rewrite it can mint sessions as any
    of their staff, so it is protected like one.
    """

    __tablename__ = "tenant_identity_providers"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=False)
    #: "oidc" or "saml".
    protocol = Column(String, nullable=False, index=True)
    enabled = Column(Boolean, default=True, nullable=False)

    # ── OIDC ────────────────────────────────────────────────────────────────
    oidc_issuer = Column(String, nullable=True)
    oidc_client_id = Column(String, nullable=True)
    oidc_client_secret = Column(String, nullable=True)

    # ── SAML ────────────────────────────────────────────────────────────────
    #: Also the routing key on the way back in: an assertion names its issuer,
    #: so the ACS endpoint can find the right organisation without a hint from
    #: the client — which is the one part of the flow the client cannot be
    #: trusted to supply.
    saml_idp_entity_id = Column(String, nullable=True, index=True)
    saml_idp_sso_url = Column(String, nullable=True)
    saml_idp_certificate = Column(String, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ErasureRecord(Base):
    """The record of a customer offboarding, and whether it actually finished.

    Deliberately outlives the tenant it describes — everything else about them
    is deleted, so if this were tenant-scoped it would be purged by the very
    operation it exists to certify.

    It holds no customer data: an id, a name, timestamps and ClickHouse
    mutation ids. That is the minimum needed to answer "was this erasure
    completed, and when", which is the question a regulator asks and which the
    purge endpoint's own response could not answer — ClickHouse deletes are
    asynchronous, so that response only ever meant "accepted".
    """

    __tablename__ = "erasure_records"

    id = Column(Integer, primary_key=True, index=True)
    #: Not a foreign key: the tenant row is gone by the time this matters.
    purged_tenant_id = Column(Integer, index=True, nullable=False)
    tenant_name = Column(String, nullable=False)
    requested_at = Column(DateTime, default=utcnow, nullable=False)
    #: Set only when every ClickHouse mutation has finished. Until then the
    #: erasure is submitted, not complete, and no certificate should be issued.
    completed_at = Column(DateTime, nullable=True)
    #: Mutation ids to check completion against.
    clickhouse_mutations = Column(JSON, default=list)
    #: Per-store outcome from the purge, including anything unreachable.
    report = Column(JSON, default=dict)
    requested_by = Column(String, nullable=True)


class ServiceLevelObjective(Base):
    __tablename__ = "slos"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    name = Column(String, nullable=False)
    service = Column(String, index=True, nullable=False)
    sli_type = Column(String, nullable=False)  # availability, latency
    target_percentage = Column(Float, nullable=False)
    window_days = Column(Integer, default=30)
    # The objective a latency SLI is measured against. Was hardcoded to 500ms in
    # the engine, which is not an SLO anyone agreed to.
    latency_threshold_ms = Column(Float, nullable=True, default=500.0)
    created_at = Column(DateTime, default=utcnow)

class SLODataPoint(Base):
    __tablename__ = "slo_data_points"

    id = Column(Integer, primary_key=True, index=True)
    slo_id = Column(Integer, index=True, nullable=False)
    timestamp = Column(DateTime, default=utcnow, index=True)
    good_events = Column(Integer, default=0)
    total_events = Column(Integer, default=0)
    value = Column(Float, nullable=False)


# ── Wave 3 Models ────────────────────────────────────────────────────────────

class Dashboard(Base):
    __tablename__ = "dashboards"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    name = Column(String, nullable=False)
    layout = Column(JSON, default=list)
    widgets = Column(JSON, default=list)
    is_shared = Column(Boolean, default=False)
    default_time_range = Column(String, default="1h")
    template_variables = Column(JSON, default=list)
    created_at = Column(DateTime, default=utcnow)

class MetricRule(Base):
    __tablename__ = "metric_rules"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)  # nullable for backwards compat
    name = Column(String, nullable=False)
    query = Column(String, nullable=False)
    aggregation = Column(String, default="count")  # count, sum, avg, max, min
    window_seconds = Column(Integer, default=60)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

class ExtractedMetric(Base):
    __tablename__ = "extracted_metrics"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    rule_id = Column(Integer, index=True, nullable=False)
    timestamp = Column(DateTime, default=utcnow, index=True)
    value = Column(Float, nullable=False)

class Runbook(Base):
    __tablename__ = "runbooks"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    name = Column(String, nullable=False)
    trigger_condition = Column(JSON, default=dict)
    steps = Column(JSON, default=list)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

class RunbookExecution(Base):
    __tablename__ = "runbook_executions"

    id = Column(Integer, primary_key=True, index=True)
    runbook_id = Column(Integer, index=True, nullable=False)
    incident_id = Column(Integer, index=True, nullable=False)
    status = Column(String, default="PENDING")  # PENDING, SUCCESS, FAILED
    logs = Column(JSON, default=list)
    created_at = Column(DateTime, default=utcnow)

class Monitor(Base):
    __tablename__ = "monitors"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    name = Column(String, nullable=False)
    type = Column(String, default="log alert") # log alert, metric alert
    query = Column(String, nullable=False)
    message = Column(String, nullable=True)
    severity = Column(String, default="warning")
    threshold_critical = Column(Float, nullable=True)
    threshold_warning = Column(Float, nullable=True)
    enabled = Column(Boolean, default=True)
    muted_until = Column(DateTime, nullable=True)  # snooze: suppress alerts until this time
    created_at = Column(DateTime, default=utcnow)
    # Evaluation state, written by the monitor evaluator. Without it a monitor
    # was a stored query nothing ever ran: the UI could only report whether it
    # was enabled, never whether it was firing.
    window_seconds = Column(Integer, default=300)
    status = Column(String, default="PENDING")  # PENDING, OK, WARNING, CRITICAL, NO_DATA, ERROR
    last_value = Column(Float, nullable=True)
    last_evaluated_at = Column(DateTime, nullable=True)
    last_triggered_at = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)


class Notebook(Base):
    __tablename__ = "notebooks"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    title = Column(String, nullable=False, default="Untitled Notebook")
    cells = Column(JSON, default=list)  # [{type, content, result?}]
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# ── Wave 4 Models ────────────────────────────────────────────────────────────

class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    api_key = Column(String, nullable=True, unique=True)
    # Rotation with an overlap window, mirroring the JWT keyring: the superseded
    # key keeps working until `api_key_previous_expires_at` so shippers can be
    # updated one at a time instead of every agent breaking at once. A leaked key
    # is revoked immediately by rotating without an overlap.
    api_key_previous = Column(String, nullable=True, index=True)
    api_key_previous_expires_at = Column(DateTime, nullable=True)
    api_key_rotated_at = Column(DateTime, nullable=True)
    tier = Column(String, default="free")  # free, pro, enterprise
    # Email domains this customer owns, lowercased and without the "@". An SSO
    # or SCIM identity is routed to the tenant that claims its domain; without
    # this, every federated user in the deployment landed in whichever tenant
    # happened to have the lowest id.
    sso_domains = Column(JSON, nullable=True, default=list)
    # Per-tenant SCIM bearer token, encrypted at rest. Which token authenticates
    # decides which company the IdP is allowed to provision into, so two
    # customers can each point their own Okta at the same deployment.
    scim_token = Column(String, nullable=True)
    scim_token_rotated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

class PlatformSetting(Base):
    """Deployment-wide operator settings, as a single JSON document.

    Previously `data/settings.json` on the API's local disk, which meant a
    second API replica could not see what the first one saved.
    """
    __tablename__ = "platform_settings"

    id = Column(Integer, primary_key=True, index=True)
    data = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class BillingMeter(Base):
    __tablename__ = "billing_meters"

    #: One row per customer per day. Metering reads-then-writes to stay
    #: idempotent, which is check-then-act and races: two beats, or a manual
    #: re-run overlapping the scheduled one, both saw "no row yet" and both
    #: inserted. A duplicate day is double-billing, so the database enforces it.
    __table_args__ = (
        UniqueConstraint("tenant_id", "date", name="uq_billing_meters_tenant_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=False)
    date = Column(DateTime, nullable=False)
    #: BigInteger, not Integer. PostgreSQL's INTEGER tops out at 2,147,483,647 —
    #: 2 GiB of bytes, which is a small day for a log platform. Exceeding it
    #: raises `NumericValueOutOfRange` on commit, and because that commit covers
    #: the whole pass, one large customer discarded every tenant's meter for
    #: that day.
    total_logs_ingested = Column(BigInteger, default=0)
    total_bytes_ingested = Column(BigInteger, default=0)
    total_traces_ingested = Column(BigInteger, default=0)
    created_at = Column(DateTime, default=utcnow)

class Plan(Base):
    """What a customer can buy.

    Priced per GB ingested: it is what the platform already meters, and it is
    what the platform's own cost tracks. A per-seat price would be simpler to
    build and would decouple revenue from a three-person team shipping forty
    terabytes.

    Money is integer minor units — cents, pence — never Float. A float price
    accumulates rounding error across a month of usage lines, and the direction
    of that error is not something you get to choose.
    """

    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    #: Stable identifier used in code and on the wire ("free", "pro", …).
    slug = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=False)
    #: Volume included before overage applies.
    included_gb = Column(Integer, nullable=False, default=0)
    #: Overage price per GB, in minor units.
    overage_price_minor = Column(Integer, nullable=False, default=0)
    #: Flat monthly platform fee, in minor units.
    base_price_minor = Column(Integer, nullable=False, default=0)
    currency = Column(String, nullable=False, default="usd")
    #: Feature slugs this plan grants. See `denoiser.api.entitlements`.
    features = Column(JSON, nullable=False, default=list)
    #: Retention granted, in days. Supersedes the tier table once a plan exists.
    retention_days = Column(Integer, nullable=False, default=7)
    #: The provider's price object, so usage can be reported against it.
    provider_price_id = Column(String, nullable=True)
    is_public = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Subscription(Base):
    """One customer's current commercial state.

    Entitlement is decided from ``status`` here, never from ``Tenant.tier``.
    A tier is a label somebody set; a status is what the payment provider says
    about whether the last invoice was paid. Gating on the label is how a
    customer keeps enterprise features after their card fails.
    """

    __tablename__ = "subscriptions"

    __table_args__ = (
        # One live subscription per customer. Two would make "which plan are
        # they on" a question with two answers, and billing questions with two
        # answers get resolved in the customer's favour by whoever asks first.
        UniqueConstraint("tenant_id", name="uq_subscriptions_tenant"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=False)
    plan_id = Column(Integer, nullable=False)

    #: "stripe", or "manual" for an invoiced enterprise agreement.
    provider = Column(String, nullable=False, default="stripe")
    provider_customer_id = Column(String, nullable=True, index=True)
    provider_subscription_id = Column(String, nullable=True, index=True)

    #: Mirrors the provider's vocabulary: trialing, active, past_due, canceled,
    #: incomplete, unpaid. Stored as given rather than mapped, so a status this
    #: code does not recognise fails closed instead of being silently coerced
    #: into one it does.
    status = Column(String, nullable=False, default="trialing")
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    #: Set when the customer has cancelled but paid through the period. They
    #: keep access until `current_period_end` — they paid for it.
    cancel_at_period_end = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ProcessedWebhookEvent(Base):
    """Every provider event this deployment has already acted on.

    Payment providers redeliver. Stripe retries a webhook for up to three days
    if it does not get a 2xx, and it will happily deliver the same event twice
    on a network hiccup where it did. A handler that upgrades a plan, or credits
    an account, must therefore be safe to run twice — and the cheapest way to be
    safe is to not run twice.

    The unique constraint is the mechanism: the insert fails on a replay, and
    the handler treats that failure as "already done".
    """

    __tablename__ = "processed_webhook_events"

    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_processed_webhook_events"),
    )

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String, nullable=False, default="stripe")
    event_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=True)
    received_at = Column(DateTime, default=utcnow)


class Integration(Base):
    __tablename__ = "integrations"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    provider = Column(String, nullable=False)  # e.g., 'slack', 'pagerduty', 'github'
    name = Column(String, nullable=False)
    config = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

class DeploymentMarker(Base):
    __tablename__ = "deployment_markers"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    service = Column(String, index=True, nullable=False)
    version = Column(String, nullable=False)
    environment = Column(String, nullable=False)
    description = Column(String, nullable=True)
    timestamp = Column(DateTime, default=utcnow, index=True)


class LogIssue(Base):
    """A log pattern tracked across analysis runs.

    A cluster only exists inside the run that produced it: its ``cluster_id``
    comes from HDBSCAN and is renumbered on every run, so the same failing
    pattern appeared as a brand-new row each time and nothing could be said
    about it beyond "it is here now". An issue is the durable identity for that
    pattern — keyed on a fingerprint of its normalized template — which is what
    makes first/last seen, an occurrence trend, triage state and assignment
    meaningful.
    """

    __tablename__ = "log_issues"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    # sha256(service|template)[:16]. Unique per tenant; the index is not itself
    # unique because two tenants legitimately share a fingerprint.
    fingerprint = Column(String, index=True, nullable=False)
    # Hashes of every template that has ever landed in this issue. A cluster's
    # representative template can drift between runs (it is whichever template
    # sits closest to the centroid), so an exact-fingerprint miss falls back to
    # intersecting these — without it, drift silently forks one issue into two.
    template_hashes = Column(JSON, default=list)

    title = Column(String, nullable=False)
    template = Column(String, nullable=True)
    representative_log = Column(String, nullable=True)
    service = Column(String, index=True, nullable=True)

    severity = Column(String, default="P3", index=True)   # P0..P3
    state = Column(String, default="FOR_REVIEW", index=True)  # FOR_REVIEW|REVIEWED|IGNORED|RESOLVED
    assignee_id = Column(Integer, nullable=True, index=True)
    team_id = Column(Integer, nullable=True, index=True)

    first_seen = Column(DateTime, default=utcnow, index=True)
    last_seen = Column(DateTime, default=utcnow, index=True)
    total_events = Column(Integer, default=0)
    run_count = Column(Integer, default=0)
    last_run_id = Column(String, nullable=True)
    last_cluster_id = Column(Integer, nullable=True)

    anomaly_score = Column(Float, default=0.0)
    is_noise = Column(Boolean, default=False)

    # {"service": [{"value": "payments", "count": 412, "pct": 83.1}, ...]}
    tags = Column(JSON, default=dict)
    # [{"ts": "2026-02-10T16:00:00+00:00", "count": 42}, ...] — hourly buckets,
    # merged across runs so the trend outlives the run that produced it.
    histogram = Column(JSON, default=list)
    # A handful of raw lines to page through in the detail panel.
    samples = Column(JSON, default=list)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class IssueComment(Base):
    __tablename__ = "issue_comments"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    issue_id = Column(Integer, index=True, nullable=False)
    user_id = Column(Integer, nullable=True)
    author_email = Column(String, nullable=True)
    body = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class IssueEvent(Base):
    """One entry in an issue's activity feed (state change, assignment, sighting)."""

    __tablename__ = "issue_events"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    issue_id = Column(Integer, index=True, nullable=False)
    user_id = Column(Integer, nullable=True)
    actor_email = Column(String, nullable=True)
    kind = Column(String, nullable=False)  # state | assignee | severity | seen | comment
    detail = Column(JSON, default=dict)
    created_at = Column(DateTime, default=utcnow, index=True)


# ── Session helpers ──────────────────────────────────────────────────────────

def init_db():
    """Bring the schema to head and seed the default tenant and admin."""
    from denoiser.storage.migrations import bootstrap_schema

    bootstrap_schema(engine)

    db = SessionLocal()
    try:
        admin_email = "admin@semanticos.io"

        # Create default tenant if not exists
        default_tenant = db.query(Tenant).filter(Tenant.name == "Default Workspace").first()
        if not default_tenant:
            default_tenant = Tenant(name="Default Workspace")
            db.add(default_tenant)
            db.commit()
            db.refresh(default_tenant)

        # Scoped to the default workspace, and resolved after it exists rather
        # than before. Unscoped, a customer who happens to employ someone at
        # admin@semanticos.io would suppress the seed for the whole deployment
        # and leave a fresh install with no way in.
        exists = (
            db.query(User)
            .filter(User.email == admin_email, User.tenant_id == default_tenant.id)
            .first()
        )

        if not exists:
            import secrets
            import sys

            import bcrypt

            _is_testing = "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ
            admin_password = os.getenv("SEMANTICOS_ADMIN_PASSWORD")
            if not admin_password:
                if _is_testing:
                    admin_password = "admin123"
                else:
                    # No default credential in production: generate a random one and
                    # tell the operator. They must reset it via SEMANTICOS_ADMIN_PASSWORD.
                    admin_password = secrets.token_urlsafe(24)
                    import logging
                    logging.getLogger("denoiser").warning(
                        "No SEMANTICOS_ADMIN_PASSWORD set; seeded %s with a random "
                        "password: %s — store it now and rotate via env.",
                        admin_email, admin_password,
                    )
            hashed = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            admin_user = User(
                email=admin_email,
                hashed_password=hashed,
                role="ADMIN",
                tenant_id=default_tenant.id,
                is_active=True,
                department="Operations",
                environment_access=["*"]
            )
            db.add(admin_user)
            db.commit()

        # Seed system-audit user
        system_email = "system-audit@semanticos.io"
        system_exists = (
            db.query(User)
            .filter(User.email == system_email, User.tenant_id == default_tenant.id)
            .first()
        )
        if not system_exists:
            import secrets

            import bcrypt
            # The system-audit user is never used for interactive login (it only
            # provides audit-log attribution), so it gets an unguessable, unusable
            # random password rather than a hardcoded one.
            hashed = bcrypt.hashpw(secrets.token_urlsafe(32).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            system_user = User(
                email=system_email,
                hashed_password=hashed,
                role="ADMIN",
                tenant_id=default_tenant.id,
                is_active=True,
                department="Security",
                environment_access=["*"]
            )
            db.add(system_user)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def get_db():
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _row_values(model, obj) -> dict:
    """The column values of a transient ORM instance, as a plain dict.

    Python-side column defaults are applied here because they normally run at
    flush time, and this helper bypasses the flush by emitting Core INSERTs.
    Without it, a column carrying `default=utcnow` would insert NULL.
    """
    values = {}
    for column in model.__table__.columns:
        value = getattr(obj, column.name, None)
        if value is None and column.default is not None:
            default = column.default
            if default.is_callable:
                value = default.arg(None)
            elif default.is_scalar:
                value = default.arg
        if value is None and column.primary_key:
            # Let the database assign it.
            continue
        values[column.name] = value
    return values


def insert_ignoring_duplicates(db, model, objects: list) -> int:
    """Insert ORM instances, silently skipping ones that already exist.

    "Already exist" means a unique constraint on the table rejected the row.
    The caller gets the number actually written.

    This exists so that an ingest endpoint can be retried safely. A client that
    resends a batch — because our response was lost, or because we asked it to
    retry after a downstream failure — must not deposit a second copy of every
    record. Ordering the writes correctly is not enough on its own: the retry
    can arrive after a fully successful request whose response never got back.

    ``ON CONFLICT DO NOTHING`` is spelled per-dialect in SQLAlchemy, and this
    project runs on both PostgreSQL and SQLite, so both are handled. Anything
    else falls back to inserting row by row inside a savepoint, which is slower
    but relies on nothing beyond the constraint itself.
    """
    if not objects:
        return 0

    rows = [_row_values(model, obj) for obj in objects]
    dialect = db.get_bind().dialect.name

    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
    else:
        written = 0
        for row in rows:
            try:
                with db.begin_nested():
                    db.execute(model.__table__.insert().values(**row))
                written += 1
            except IntegrityError:
                continue
        return written

    result = db.execute(_insert(model.__table__).on_conflict_do_nothing(), rows)
    # `rowcount` is the number of rows the statement actually inserted; the
    # conflicting ones are not counted. Drivers that decline to report it give
    # -1, in which case the batch size is the closest honest answer.
    return len(rows) if result.rowcount is None or result.rowcount < 0 else result.rowcount
