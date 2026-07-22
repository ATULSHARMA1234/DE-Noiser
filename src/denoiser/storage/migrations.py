"""
Schema bootstrap.

Alembic owns the schema. This module decides *how* to get a given database to
head, because there are three distinct starting states and they need different
handling:

  fresh     no tables at all           -> upgrade head builds everything
  managed   alembic_version present    -> upgrade head applies what's missing
  legacy    tables but no stamp        -> repair, then stamp head

The legacy case exists because the schema used to be created by
``Base.metadata.create_all()`` with columns bolted on by ad-hoc ``ALTER TABLE``
probes at startup. Those databases are real (the deployed instance is one), and
they must not be re-created. But they also cannot simply be stamped: create_all
never alters an existing table, so a database created before a column was added
to the models is missing that column, and stamping it head would assert a
schema it does not have. So legacy databases are repaired to the baseline first,
then stamped.

Once stamped, a database is 'managed' forever and this file's legacy path is
never taken again.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.sql.expression import true

from denoiser.logging import get_logger

logger = get_logger(__name__)

# Columns that the ad-hoc startup probes used to add. A database created by
# create_all at any point in that history may be missing any subset of them, so
# each is probed and added independently — gating several columns behind one
# probe silently skips the rest on a database that already has the first.
#
# Declared as SQLAlchemy types rather than DDL strings so alembic renders them
# per dialect. Hand-written DDL was SQLite-shaped ("BOOLEAN DEFAULT 1",
# "DATETIME") and Postgres rejects both — which is the dialect the deployed
# instance actually runs.
#
# ``index`` is only set where the model declares one. A blanket index breaks on
# Postgres anyway: json columns have no default btree operator class.
LEGACY_COLUMNS: list[tuple[str, str, Callable[[], Column], bool]] = [
    ("users", "is_active", lambda: Column("is_active", Boolean(), server_default=true(), nullable=True), False),
    ("users", "department", lambda: Column("department", String(), server_default="Engineering", nullable=True), False),
    ("users", "environment_access", lambda: Column("environment_access", JSON(), nullable=True), False),
    ("dashboards", "default_time_range", lambda: Column("default_time_range", String(), server_default="1h", nullable=True), False),
    ("dashboards", "template_variables", lambda: Column("template_variables", JSON(), nullable=True), False),
    ("monitors", "muted_until", lambda: Column("muted_until", DateTime(), nullable=True), False),
    ("metric_rules", "tenant_id", lambda: Column("tenant_id", Integer(), nullable=True), True),
    ("extracted_metrics", "tenant_id", lambda: Column("tenant_id", Integer(), nullable=True), True),
    ("spans", "tenant_id", lambda: Column("tenant_id", Integer(), nullable=True), True),
]


def _alembic_config():
    """Load alembic.ini from the repo root, or None when running from a wheel.

    The alembic directory ships with the source tree, not the built package, so
    an installed-as-a-library consumer has no migrations to run. Callers fall
    back to create_all in that case.
    """
    try:
        from alembic.config import Config
    except ImportError:
        logger.warning("alembic is not installed; falling back to create_all")
        return None

    # src/denoiser/storage/migrations.py -> repo root is three parents up.
    ini_path = Path(__file__).resolve().parents[3] / "alembic.ini"
    if not ini_path.is_file():
        logger.warning(f"alembic.ini not found at {ini_path}; falling back to create_all")
        return None

    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(ini_path.parent / "alembic"))
    return cfg


def _current_revision(engine: Engine) -> str | None:
    """The revision this database is stamped at, or None if it has no stamp."""
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _run_alembic(cfg, engine: Engine, run) -> None:
    """Run an alembic command against our connection, in its own transaction.

    Alembic must not share a transaction with the repair steps: if a later step
    fails, the rollback has to take the stamp with it or leave it untouched,
    never half of each.
    """
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        run()


def _repair_legacy_columns(engine: Engine) -> list[str]:
    """Add any baseline columns a create_all-era database is missing.

    Uses alembic's operations rather than raw DDL so the column types render
    correctly for whichever dialect is in use.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    repaired = []

    for table, column, build_column, wants_index in LEGACY_COLUMNS:
        if table not in existing_tables:
            continue  # create_all will add the whole table below
        if column in {c["name"] for c in inspector.get_columns(table)}:
            continue

        with engine.begin() as conn:
            op = Operations(MigrationContext.configure(conn))
            op.add_column(table, build_column())
            if wants_index:
                op.create_index(f"ix_{table}_{column}", table, [column])
        repaired.append(f"{table}.{column}")

    return repaired


def bootstrap_schema(engine: Engine) -> str:
    """Bring ``engine``'s database to head. Returns the path taken.

    Set SEMANTICOS_AUTO_MIGRATE=0 to skip this entirely and manage the schema
    out of band (``alembic upgrade head`` as a deploy step), which is the safer
    shape once more than one API instance can start at the same time.
    """
    if os.getenv("SEMANTICOS_AUTO_MIGRATE", "1") not in ("1", "true", "True"):
        logger.info("SEMANTICOS_AUTO_MIGRATE is off; leaving the schema alone")
        return "skipped"

    from denoiser.storage.db import Base

    cfg = _alembic_config()
    if cfg is None:
        # No migrations available (installed as a library, or alembic missing).
        Base.metadata.create_all(bind=engine)
        _repair_legacy_columns(engine)
        return "create_all"

    from alembic import command

    table_names = set(inspect(engine).get_table_names())
    real_tables = table_names - {"alembic_version"}

    # An alembic_version table with no row is not a stamp: alembic reads it as
    # 'at base' and replays the baseline against tables that already exist. It
    # happens when a stamp is interrupted, so treat it as unstamped and adopt.
    stamped = _current_revision(engine) is not None

    if stamped:
        _run_alembic(cfg, engine, lambda: command.upgrade(cfg, "head"))
        return "managed"

    if not real_tables:
        _run_alembic(cfg, engine, lambda: command.upgrade(cfg, "head"))
        logger.info("Fresh database created from migrations")
        return "fresh"

    # Legacy: pre-migration database. Repair to the baseline, then adopt it.
    # These run in their own transactions, outside the one alembic gets — a
    # failure part-way must not leave a half-written stamp behind.
    repaired = _repair_legacy_columns(engine)
    # create_all covers tables added to the models after this database was last
    # started; it is a no-op for tables that already exist.
    Base.metadata.create_all(bind=engine)
    _run_alembic(cfg, engine, lambda: command.stamp(cfg, "head"))
    logger.info(
        f"Adopted existing database into alembic (repaired: {', '.join(repaired) if repaired else 'nothing'})"
    )
    return "legacy-adopted"
