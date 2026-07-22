"""
Legacy-adoption check against a real Postgres.

Run as a module (``python -m tests.postgres_legacy_check``) with DATABASE_URL
pointing at a disposable Postgres database. It is not a pytest test because the
unit suite must stay runnable with no services; this is a CI gate that needs a
live server.

It exists because the repair DDL was originally SQLite-shaped — "BOOLEAN
DEFAULT 1", "DATETIME", and a blanket index that Postgres refuses on json
columns. Every SQLite test passed while the path that upgrades a real deployment
was broken. Only a real Postgres catches that.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, inspect, text

from denoiser.storage.migrations import LEGACY_COLUMNS, bootstrap_schema

# A create_all-era database: tables present, ad-hoc columns absent, no stamp.
LEGACY_SCHEMA = """
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
CREATE TABLE users (id SERIAL PRIMARY KEY, email VARCHAR, hashed_password VARCHAR, role VARCHAR, tenant_id INTEGER);
CREATE TABLE monitors (id SERIAL PRIMARY KEY, name VARCHAR);
CREATE TABLE dashboards (id SERIAL PRIMARY KEY, name VARCHAR);
CREATE TABLE spans (id SERIAL PRIMARY KEY, trace_id VARCHAR, span_id VARCHAR, service_name VARCHAR,
                    operation_name VARCHAR, start_time TIMESTAMP, end_time TIMESTAMP, duration_ms FLOAT);
CREATE TABLE metric_rules (id SERIAL PRIMARY KEY, name VARCHAR, query VARCHAR);
CREATE TABLE extracted_metrics (id SERIAL PRIMARY KEY, rule_id INTEGER);
INSERT INTO users (email, role) VALUES ('existing@customer.io', 'ADMIN');
INSERT INTO spans (trace_id, span_id, service_name, operation_name) VALUES ('t1', 's1', 'checkout', '/pay');
"""


def main() -> int:
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        print(f"SKIP: DATABASE_URL is not Postgres ({url!r})")
        return 0

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(LEGACY_SCHEMA))

    failures: list[str] = []

    path = bootstrap_schema(engine)
    if path != "legacy-adopted":
        failures.append(f"expected 'legacy-adopted', got {path!r}")

    rerun = bootstrap_schema(engine)
    if rerun != "managed":
        failures.append(f"restart should be 'managed', got {rerun!r}")

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, column, _build, _index in LEGACY_COLUMNS:
        if table not in tables:
            failures.append(f"{table} missing entirely")
        elif column not in {c["name"] for c in inspector.get_columns(table)}:
            failures.append(f"{table}.{column} was not repaired")

    with engine.connect() as conn:
        if conn.execute(text("SELECT email FROM users")).scalar() != "existing@customer.io":
            failures.append("existing user row was lost")
        if conn.execute(text("SELECT service_name FROM spans")).scalar() != "checkout":
            failures.append("existing span row was lost")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print(f"OK: legacy Postgres database adopted and repaired ({len(LEGACY_COLUMNS)} columns checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
