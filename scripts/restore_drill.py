#!/usr/bin/env python3
"""Prove the backups restore. Measures the RTO while doing it.

`docs/operations.md` carried correct `pg_dump` and `clickhouse-client` commands
for a long time and nothing ever ran them end to end. A backup nobody has
restored is a hypothesis, and the way that hypothesis usually fails is not
"the dump was corrupt" — it is that the restore misses a store, or brings back
a schema with no rows, and nobody notices until the day it matters.

So this runs the whole loop against live datastores:

  1. **Seed** a recognisable dataset in Postgres and ClickHouse.
  2. **Back up** using the same commands the Helm CronJob runs. Not a
     re-implementation — a paraphrase would prove the paraphrase works.
  3. **Destroy** both stores. Dropping the schema, not deleting rows: a restore
     into a database that still has its tables can pass while the real
     disaster-recovery path fails on the DDL.
  4. **Restore**, and time it.
  5. **Verify** the data is actually back — row counts *and* content, because a
     restore that recreates the schema and no rows passes every check except
     reading a record.

Exit codes: 0 the restore is proven, 1 it is not, 2 the environment is missing.

    python scripts/restore_drill.py \
        --postgres postgresql://drill:drill@localhost:55432/semanticos_drill \
        --clickhouse http://localhost:58123
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

MARKER = "restore-drill-canary"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False, **kwargs)


def split_credentials(url: str) -> tuple[str, dict[str, str]]:
    """Separate ``http://user:pass@host:port`` into a URL and auth headers.

    ``urllib.request.urlopen`` does not understand userinfo in a URL — it hands
    the whole ``user:pass@host`` string to the resolver and fails with
    "nodename nor servname provided". So a drill pointed at any ClickHouse with
    a password could not run at all, which is every deployment that resembles
    production. The drill only ever worked against an unauthenticated server.

    ClickHouse accepts credentials as ``X-ClickHouse-User`` / ``X-ClickHouse-Key``
    headers, which keeps them out of the request line (and therefore out of the
    server's query log) as well.
    """
    parsed = urllib.parse.urlsplit(url)
    if not parsed.username and not parsed.password:
        return url.rstrip("/"), {}

    host = parsed.hostname or "localhost"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    clean = urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))

    headers = {}
    if parsed.username:
        headers["X-ClickHouse-User"] = urllib.parse.unquote(parsed.username)
    if parsed.password:
        headers["X-ClickHouse-Key"] = urllib.parse.unquote(parsed.password)
    return clean.rstrip("/"), headers


def clickhouse_raw(url: str, query: str, body: bytes = b"", headers: dict | None = None) -> bytes:
    """Issue a query over the HTTP interface, which needs no client binary.

    Returns bytes. `FORMAT Native` — which is what the backup uses, because it
    round-trips types exactly where a text format does not — is binary, so
    decoding the response as UTF-8 fails on the first row.
    """
    base, auth = split_credentials(url)
    request = urllib.request.Request(
        f"{base}/?query={urllib.parse.quote(query)}",
        data=body,
        method="POST",
        headers={**auth, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ClickHouse rejected {query[:80]!r}: {exc.read().decode()}") from exc


def clickhouse(url: str, query: str, body: str | None = None) -> str:
    """Text-returning wrapper, for queries whose output is human-readable."""
    return clickhouse_raw(url, query, (body or "").encode()).decode()


def step(message: str) -> None:
    print(f"\n── {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup and restore drill")
    parser.add_argument("--postgres", required=True)
    parser.add_argument("--clickhouse", required=True)
    parser.add_argument("--rows", type=int, default=5_000,
                        help="rows to seed per store; the RTO scales with this")
    parser.add_argument("--report", type=Path, default=None, help="write the result as JSON")
    args = parser.parse_args()

    for binary in ("pg_dump", "pg_restore", "psql"):
        if not shutil.which(binary):
            print(f"error: {binary} is not on PATH", file=sys.stderr)
            return 2

    seed_rows = args.rows
    work = Path(tempfile.mkdtemp(prefix="restore-drill-"))
    result: dict = {"started_at": datetime.now(UTC).isoformat()}

    # ── 1. Seed ──────────────────────────────────────────────────────────────
    step(f"Seeding {seed_rows:,} rows")
    seed_sql = f"""
        DROP SCHEMA IF EXISTS public CASCADE;
        CREATE SCHEMA public;
        CREATE TABLE incidents (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        INSERT INTO incidents (tenant_id, title)
        SELECT (i % 3) + 1, '{MARKER}-' || i FROM generate_series(1, {seed_rows}) AS i;
    """
    seeded = run(["psql", args.postgres, "-v", "ON_ERROR_STOP=1", "-c", seed_sql])
    if seeded.returncode != 0:
        print(seeded.stderr, file=sys.stderr)
        return 1

    clickhouse(args.clickhouse, "DROP TABLE IF EXISTS semantic_logs")
    clickhouse(
        args.clickhouse,
        "CREATE TABLE semantic_logs (tenant_id String, timestamp DateTime64(3,'UTC'), "
        "source String, level String, message String, raw_json String) "
        "ENGINE = MergeTree() ORDER BY (tenant_id, source, timestamp)",
    )
    rows = "\n".join(
        json.dumps({
            "tenant_id": str((i % 3) + 1),
            "timestamp": "2026-08-04 12:00:00.000",
            "source": "drill",
            "level": "ERROR",
            "message": f"{MARKER}-{i}",
            "raw_json": "{}",
        })
        for i in range(seed_rows)
    )
    clickhouse(args.clickhouse, "INSERT INTO semantic_logs FORMAT JSONEachRow", rows)

    pg_before = int(run(["psql", args.postgres, "-tAc", "SELECT count(*) FROM incidents"]).stdout.strip())
    ch_before = int(clickhouse(args.clickhouse, "SELECT count() FROM semantic_logs").strip())
    print(f"   postgres={pg_before:,}  clickhouse={ch_before:,}")
    result["seeded"] = {"postgres": pg_before, "clickhouse": ch_before}

    # ── 2. Back up, with the CronJob's own commands ──────────────────────────
    step("Backing up")
    backup_started = time.perf_counter()
    dump = work / "postgres.dump"
    dumped = run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges",
                  "--file", str(dump), args.postgres])
    if dumped.returncode != 0:
        print(dumped.stderr, file=sys.stderr)
        return 1

    # TabSeparatedRaw, not the default. SHOW CREATE TABLE returns one String
    # column, and TabSeparated escapes the newlines inside it — so the saved
    # DDL comes back as a single line containing a literal "\n" and ClickHouse
    # refuses it on restore with a syntax error. The backup CronJob had the
    # same defect; this drill is how it was found.
    schema = clickhouse(
        args.clickhouse, "SHOW CREATE TABLE semantic_logs FORMAT TabSeparatedRaw"
    )
    (work / "clickhouse.schema.sql").write_text(schema)
    native = clickhouse_raw(args.clickhouse, "SELECT * FROM semantic_logs FORMAT Native")
    (work / "clickhouse.native").write_bytes(native)
    backup_seconds = time.perf_counter() - backup_started

    sizes = {p.name: p.stat().st_size for p in work.iterdir()}
    print(f"   {backup_seconds:.1f}s, {sum(sizes.values()):,} bytes: {sizes}")
    result["backup_seconds"] = round(backup_seconds, 2)
    result["backup_bytes"] = sum(sizes.values())

    # ── 3. Destroy ───────────────────────────────────────────────────────────
    step("Destroying both stores")
    run(["psql", args.postgres, "-v", "ON_ERROR_STOP=1", "-c",
         "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"])
    clickhouse(args.clickhouse, "DROP TABLE semantic_logs")

    surviving = run(["psql", args.postgres, "-tAc",
                     "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"])
    assert surviving.stdout.strip() == "0", "postgres was not actually emptied"
    print("   both stores are empty")

    # ── 4. Restore, timed ────────────────────────────────────────────────────
    step("Restoring")
    restore_started = time.perf_counter()

    restored = run(["pg_restore", "--clean", "--if-exists", "--no-owner",
                    "-d", args.postgres, str(dump)])
    if restored.returncode != 0 and "errors ignored" not in restored.stderr:
        print(restored.stderr, file=sys.stderr)

    create = (work / "clickhouse.schema.sql").read_text().strip()
    clickhouse(args.clickhouse, create)
    clickhouse_raw(args.clickhouse, "INSERT INTO semantic_logs FORMAT Native",
                   (work / "clickhouse.native").read_bytes())

    restore_seconds = time.perf_counter() - restore_started
    print(f"   {restore_seconds:.1f}s")
    result["restore_seconds"] = round(restore_seconds, 2)

    # ── 5. Verify ────────────────────────────────────────────────────────────
    step("Verifying")
    failures: list[str] = []

    pg_after = int(run(["psql", args.postgres, "-tAc", "SELECT count(*) FROM incidents"]).stdout.strip() or 0)
    ch_after = int(clickhouse(args.clickhouse, "SELECT count() FROM semantic_logs").strip() or 0)
    print(f"   rows: postgres={pg_after:,} (was {pg_before:,})  clickhouse={ch_after:,} (was {ch_before:,})")

    if pg_after != pg_before:
        failures.append(f"postgres restored {pg_after} of {pg_before} rows")
    if ch_after != ch_before:
        failures.append(f"clickhouse restored {ch_after} of {ch_before} rows")

    # Content, not just counts: a restore that recreates the schema and no rows
    # passes a count check against an empty expectation, and a restore that
    # brings back the wrong rows passes a count check entirely.
    sample = run(["psql", args.postgres, "-tAc",
                  f"SELECT title FROM incidents WHERE title = '{MARKER}-42'"]).stdout.strip()
    if sample != f"{MARKER}-42":
        failures.append("a known Postgres record did not come back")

    ch_sample = clickhouse(
        args.clickhouse,
        f"SELECT count() FROM semantic_logs WHERE message = '{MARKER}-42'",
    ).strip()
    if ch_sample != "1":
        failures.append("a known ClickHouse record did not come back")

    # Tenant attribution has to survive: a restore that loses it deposits one
    # customer's data into everybody's account.
    tenants = clickhouse(
        args.clickhouse, "SELECT count(DISTINCT tenant_id) FROM semantic_logs"
    ).strip()
    if tenants != "3":
        failures.append(f"tenant attribution did not survive: {tenants} distinct tenants, expected 3")

    shutil.rmtree(work, ignore_errors=True)

    result["passed"] = not failures
    result["failures"] = failures
    result["finished_at"] = datetime.now(UTC).isoformat()
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n")

    print()
    if failures:
        print("DRILL FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"DRILL PASSED — restore took {restore_seconds:.1f}s "
          f"for {pg_before + ch_before:,} rows across both stores")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
