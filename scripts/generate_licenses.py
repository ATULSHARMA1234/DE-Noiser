#!/usr/bin/env python3
"""Regenerate the dependency table in THIRD_PARTY_LICENSES.md.

Only the table. The positions above it — psycopg2's LGPL, the MPL components,
Redpanda's BSL, Redis and MinIO — are judgements with expiry conditions
attached, and a generator would flatten them into licence strings that answer
none of the questions procurement actually asks.

    uv run python scripts/generate_licenses.py           # print the table
    uv run python scripts/generate_licenses.py --check    # fail if it is stale
"""

from __future__ import annotations

import importlib.metadata as md
import sys
from pathlib import Path

DOC = Path("THIRD_PARTY_LICENSES.md")
TABLE_HEADER = "| Package | Version | License |"


def rows() -> list[str]:
    seen: dict[str, tuple[str, str, str]] = {}
    for dist in md.distributions():
        meta = dist.metadata
        name = meta["Name"]
        if not name:
            continue
        classifiers = [
            c.replace("License :: OSI Approved :: ", "").replace("License :: ", "")
            for c in (meta.get_all("Classifier") or [])
            if c.startswith("License")
        ]
        declared = meta["License"]
        fallback = declared.strip().splitlines()[0][:40] if declared else ""
        resolved = classifiers[0] if classifiers else (fallback or "see package metadata")
        seen[name.lower()] = (name, dist.version, resolved)

    return [
        f"| {name} | {version} | {lic} |"
        for name, version, lic in sorted(seen.values(), key=lambda r: r[0].lower())
    ]


def main(argv: list[str]) -> int:
    table = rows()

    if "--check" not in argv:
        print("\n".join(table))
        return 0

    if not DOC.exists():
        print(f"{DOC} does not exist", file=sys.stderr)
        return 1

    current = DOC.read_text()
    if TABLE_HEADER not in current:
        print(f"{DOC} has no dependency table to compare against", file=sys.stderr)
        return 1

    existing = {
        line for line in current.splitlines()
        if line.startswith("| ") and line.count("|") == 4
    }
    missing = [line for line in table if line not in existing]
    if missing:
        print(f"{DOC} is out of date. Missing or changed:", file=sys.stderr)
        for line in missing[:20]:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nRegenerate: uv run python scripts/generate_licenses.py",
            file=sys.stderr,
        )
        return 1

    print(f"{DOC} is current ({len(table)} packages).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
