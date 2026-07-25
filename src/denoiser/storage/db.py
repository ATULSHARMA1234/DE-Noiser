"""
Database models and session management for SemanticOS.

Task 5: Supports both SQLite (local dev) and PostgreSQL (production).
The DATABASE_URL is read from environment variables / .env file.
"""

from __future__ import annotations

import os

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String, create_engine
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

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
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
    webhook_id = Column(String, index=True)
    alert_fingerprint = Column(String, index=True)
    priority = Column(String)
    status = Column(String)
    http_status = Column(Integer, nullable=True)
    latency_ms = Column(Float, nullable=True)
    error = Column(String, nullable=True)
    timestamp = Column(String, default=lambda: utcnow().isoformat(), index=True)

# ── Wave 2 Models ────────────────────────────────────────────────────────────

class Span(Base):
    __tablename__ = "spans"

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
    name = Column(String, nullable=False)
    query_text = Column(String, nullable=False)
    user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    last_used = Column(DateTime, default=utcnow)

class ServiceLevelObjective(Base):
    __tablename__ = "slos"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    name = Column(String, nullable=False)
    service = Column(String, index=True, nullable=False)
    sli_type = Column(String, nullable=False)  # availability, latency
    target_percentage = Column(Float, nullable=False)
    window_days = Column(Integer, default=30)
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
    tier = Column(String, default="free")  # free, pro, enterprise
    created_at = Column(DateTime, default=utcnow)

class BillingMeter(Base):
    __tablename__ = "billing_meters"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=False)
    date = Column(DateTime, nullable=False)
    total_logs_ingested = Column(Integer, default=0)
    total_bytes_ingested = Column(Integer, default=0)
    total_traces_ingested = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)

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


# ── Session helpers ──────────────────────────────────────────────────────────

def init_db():
    """Bring the schema to head and seed the default tenant and admin."""
    from denoiser.storage.migrations import bootstrap_schema

    bootstrap_schema(engine)

    db = SessionLocal()
    try:
        admin_email = "admin@semanticos.io"
        exists = db.query(User).filter(User.email == admin_email).first()

        # Create default tenant if not exists
        default_tenant = db.query(Tenant).filter(Tenant.name == "Default Workspace").first()
        if not default_tenant:
            default_tenant = Tenant(name="Default Workspace")
            db.add(default_tenant)
            db.commit()
            db.refresh(default_tenant)

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
        system_exists = db.query(User).filter(User.email == system_email).first()
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
