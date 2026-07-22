"""
Tests for the schema bootstrap.

The three starting states a real database can be in — fresh, legacy
(create_all-era, unstamped) and managed — take different paths, and getting the
legacy one wrong destroys or corrupts a live customer database. So each is
exercised against a real SQLite file rather than a mock.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, text

from denoiser.storage.migrations import LEGACY_COLUMNS, bootstrap_schema

# A create_all-era database: the tables exist, but every column the old ad-hoc
# ALTER ladder used to add is missing, and there is no alembic_version stamp.
LEGACY_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR, hashed_password VARCHAR, role VARCHAR, tenant_id INTEGER);
CREATE TABLE monitors (id INTEGER PRIMARY KEY, name VARCHAR);
CREATE TABLE dashboards (id INTEGER PRIMARY KEY, name VARCHAR);
CREATE TABLE spans (id INTEGER PRIMARY KEY, trace_id VARCHAR, span_id VARCHAR, service_name VARCHAR,
                    operation_name VARCHAR, start_time DATETIME, end_time DATETIME, duration_ms FLOAT);
CREATE TABLE metric_rules (id INTEGER PRIMARY KEY, name VARCHAR, query VARCHAR);
CREATE TABLE extracted_metrics (id INTEGER PRIMARY KEY, rule_id INTEGER);
INSERT INTO users (email, role) VALUES ('existing@customer.io', 'ADMIN');
INSERT INTO spans (trace_id, span_id, service_name, operation_name) VALUES ('t1', 's1', 'checkout', '/pay');
"""


@pytest.fixture
def fresh_engine(tmp_path):
    return create_engine(f"sqlite:///{tmp_path}/fresh.db")


@pytest.fixture
def legacy_engine(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.commit()
    conn.close()
    return create_engine(f"sqlite:///{path}")


class TestFreshDatabase:
    def test_builds_the_whole_schema_from_migrations(self, fresh_engine):
        assert bootstrap_schema(fresh_engine) == "fresh"

        tables = set(inspect(fresh_engine).get_table_names())
        assert "alembic_version" in tables
        # 20 model tables + alembic_version
        assert len(tables) == 21

    def test_includes_columns_that_predate_the_migrations(self, fresh_engine):
        """These only ever existed via ad-hoc ALTERs; the baseline must carry them."""
        bootstrap_schema(fresh_engine)

        inspector = inspect(fresh_engine)
        for table, column, _build, _index in LEGACY_COLUMNS:
            assert column in {c["name"] for c in inspector.get_columns(table)}, f"{table}.{column} missing"


class TestLegacyDatabase:
    def test_is_adopted_not_recreated(self, legacy_engine):
        assert bootstrap_schema(legacy_engine) == "legacy-adopted"

    def test_preserves_existing_data(self, legacy_engine):
        bootstrap_schema(legacy_engine)

        with legacy_engine.connect() as conn:
            assert conn.execute(text("SELECT email FROM users")).scalar() == "existing@customer.io"
            assert conn.execute(text("SELECT service_name FROM spans")).scalar() == "checkout"

    def test_repairs_every_missing_column(self, legacy_engine):
        """The failure this guards against is a model declaring a column the table lacks."""
        bootstrap_schema(legacy_engine)

        inspector = inspect(legacy_engine)
        for table, column, _build, _index in LEGACY_COLUMNS:
            assert column in {c["name"] for c in inspector.get_columns(table)}, f"{table}.{column} not repaired"

    def test_creates_tables_added_since(self, legacy_engine):
        """The legacy fixture has no notebooks table; adoption must add it."""
        bootstrap_schema(legacy_engine)

        assert "notebooks" in set(inspect(legacy_engine).get_table_names())


class TestIdempotency:
    def test_second_run_is_managed_and_harmless(self, legacy_engine):
        """A restart must not replay the baseline against a populated database."""
        bootstrap_schema(legacy_engine)

        assert bootstrap_schema(legacy_engine) == "managed"
        assert bootstrap_schema(legacy_engine) == "managed"

        with legacy_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1

    def test_fresh_database_restarts_cleanly(self, fresh_engine):
        bootstrap_schema(fresh_engine)
        assert bootstrap_schema(fresh_engine) == "managed"


class TestOptOut:
    def test_auto_migrate_can_be_disabled(self, fresh_engine, monkeypatch):
        """Operators running migrations as a deploy step must be able to opt out."""
        monkeypatch.setenv("SEMANTICOS_AUTO_MIGRATE", "0")

        assert bootstrap_schema(fresh_engine) == "skipped"
        assert inspect(fresh_engine).get_table_names() == []
