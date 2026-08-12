#!/usr/bin/env python3
"""Fail if any outbound HTTP call is made without a timeout.

`requests` has no default timeout. A call without one blocks forever against a
host that accepts the connection and never replies, which is what a partial
third-party outage looks like from here. The Slack reporter was missing one, and
the failure mode was a Celery pool that drained a slot per analysis run until
the pipeline stopped, with nothing raising and nothing to alert on.

Every other call site in the codebase already passed a timeout. This exists so
that stays true — one missing keyword argument is not something review reliably
catches, and `ruff` has no rule for it.

Run: python scripts/check_request_timeouts.py [paths...]
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: Callables that open a connection and accept a `timeout` keyword.
_HTTP_CALLS = {
    ("requests", "get"), ("requests", "post"), ("requests", "put"),
    ("requests", "patch"), ("requests", "delete"), ("requests", "head"),
    ("requests", "request"),
    ("httpx", "get"), ("httpx", "post"), ("httpx", "put"),
    ("httpx", "patch"), ("httpx", "delete"), ("httpx", "head"),
    ("httpx", "request"), ("httpx", "stream"),
    ("httpx", "Client"), ("httpx", "AsyncClient"),
}


def _offenders(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Only `module.attr(...)`, so `self._requests.get(...)` — a dict lookup
        # that happens to share the name — is not mistaken for an HTTP call.
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            continue
        pair = (func.value.id, func.attr)
        if pair not in _HTTP_CALLS:
            continue
        if any(kw.arg == "timeout" for kw in node.keywords):
            continue
        # `**kwargs` may carry it; too dynamic to judge, so it is allowed.
        if any(kw.arg is None for kw in node.keywords):
            continue
        found.append((node.lineno, f"{pair[0]}.{pair[1]}"))
    return found


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] or [Path("src")]
    failures: list[str] = []

    for root in roots:
        files = root.rglob("*.py") if root.is_dir() else [root]
        for path in files:
            try:
                tree = ast.parse(path.read_text(), filename=str(path))
            except SyntaxError as exc:
                failures.append(f"{path}: could not parse ({exc})")
                continue
            for lineno, call in _offenders(tree):
                failures.append(f"{path}:{lineno}: {call}(...) has no timeout=")

    if failures:
        print("Outbound HTTP calls without a timeout:")
        for failure in failures:
            print(f"  {failure}")
        print(
            "\nPass timeout=<seconds>. A call with no timeout waits forever on a "
            "host that never answers, and takes a worker slot with it."
        )
        return 1

    print("All outbound HTTP calls pass a timeout.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
