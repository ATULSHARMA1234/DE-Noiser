"""
Database models and session management for SemanticOS.

Task 5: Supports both SQLite (local dev) and PostgreSQL (production).
The DATABASE_URL is read from environment variables / .env file.
"""

from __future__ import annotations

import datetime
import os

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON, Boolean
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
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="VIEWER", nullable=False)  # ADMIN, ANALYST, VIEWER


# ── Session helpers ──────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they don't exist and seed default admin."""
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        admin_email = "admin@semanticos.io"
        exists = db.query(User).filter(User.email == admin_email).first()
        if not exists:
            from passlib.hash import bcrypt
            admin_user = User(
                email=admin_email,
                hashed_password=bcrypt.hash("admin123"),
                role="ADMIN"
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
