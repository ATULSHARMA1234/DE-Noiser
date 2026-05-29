"""
Database models and session management for SemanticOS.

Task 5: Supports both SQLite (local dev) and PostgreSQL (production).
The DATABASE_URL is read from environment variables / .env file.
"""

from __future__ import annotations

import datetime
import os

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# ── Task 5: Dual-database support ───────────────────────────────────────────
# Default to SQLite for zero-config local development.
# Set DATABASE_URL=postgresql://user:pass@host:5432/semanticos for production.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/semantic_os.db")

# SQLite requires check_same_thread=False; PostgreSQL does not.
_connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, connect_args=_connect_args)
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
    impact_score = Column(Float)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    # Intelligence
    summary = Column(JSON)  # List of bullet points or string
    remediation_hints = Column(JSON)  # List of hints

    # Linked analysis context
    run_id = Column(String, nullable=True)
    source = Column(String, nullable=True)
    total_logs = Column(Integer, nullable=True)
    cluster_count = Column(Integer, nullable=True)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id = Column(String, primary_key=True, index=True)  # e.g. run_a1b2c3d4
    source = Column(String)
    status = Column(String)
    raw_lines = Column(Integer)
    cluster_count = Column(Integer)
    reduction_ratio = Column(Float)
    duration_sec = Column(Float)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    clusters_snapshot = Column(JSON, nullable=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="VIEWER", nullable=False)  # ADMIN, ANALYST, VIEWER


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)  # Nullable for unauthenticated actions
    action = Column(String, nullable=False)
    resource_type = Column(String, nullable=False)
    resource_id = Column(String, nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)

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
    timestamp = Column(String, default=lambda: datetime.datetime.utcnow().isoformat(), index=True)

# ── Wave 2 Models ────────────────────────────────────────────────────────────

class Span(Base):
    __tablename__ = "spans"

    id = Column(Integer, primary_key=True, index=True)
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
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_used = Column(DateTime, default=datetime.datetime.utcnow)

class ServiceLevelObjective(Base):
    __tablename__ = "slos"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    service = Column(String, index=True, nullable=False)
    sli_type = Column(String, nullable=False)  # availability, latency
    target_percentage = Column(Float, nullable=False)
    window_days = Column(Integer, default=30)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class SLODataPoint(Base):
    __tablename__ = "slo_data_points"

    id = Column(Integer, primary_key=True, index=True)
    slo_id = Column(Integer, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    good_events = Column(Integer, default=0)
    total_events = Column(Integer, default=0)
    value = Column(Float, nullable=False)


# ── Wave 3 Models ────────────────────────────────────────────────────────────

class Dashboard(Base):
    __tablename__ = "dashboards"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    name = Column(String, nullable=False)
    user_id = Column(Integer, index=True, nullable=False)
    layout = Column(JSON, default=list)
    widgets = Column(JSON, default=list)
    is_shared = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class MetricRule(Base):
    __tablename__ = "metric_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    query = Column(String, nullable=False)
    aggregation = Column(String, default="count")  # count, sum, avg, max, min
    window_seconds = Column(Integer, default=60)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class ExtractedMetric(Base):
    __tablename__ = "extracted_metrics"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    value = Column(Float, nullable=False)

class Runbook(Base):
    __tablename__ = "runbooks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    trigger_condition = Column(JSON, default=dict)
    steps = Column(JSON, default=list)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class RunbookExecution(Base):
    __tablename__ = "runbook_executions"

    id = Column(Integer, primary_key=True, index=True)
    runbook_id = Column(Integer, index=True, nullable=False)
    incident_id = Column(Integer, index=True, nullable=False)
    status = Column(String, default="PENDING")  # PENDING, SUCCESS, FAILED
    logs = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ── Wave 4 Models ────────────────────────────────────────────────────────────

class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Integration(Base):
    __tablename__ = "integrations"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    provider = Column(String, nullable=False)  # e.g., 'slack', 'pagerduty', 'github'
    name = Column(String, nullable=False)
    config = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class DeploymentMarker(Base):
    __tablename__ = "deployment_markers"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    service = Column(String, index=True, nullable=False)
    version = Column(String, nullable=False)
    environment = Column(String, nullable=False)
    description = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)


# ── Session helpers ──────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they don't exist and seed default admin."""
    Base.metadata.create_all(bind=engine)

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
            import bcrypt
            hashed = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8")
            admin_user = User(
                email=admin_email,
                hashed_password=hashed,
                role="ADMIN",
                tenant_id=default_tenant.id
            )
            db.add(admin_user)
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
