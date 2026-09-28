#!/usr/bin/env python3
"""Fail a build when performance regresses against a recorded baseline.

`loadtest.py` measured throughput and latency and printed them for a human to
read. Nothing compared one run to the last, so a change that halved ingest
throughput passed every gate in CI — the suite would be green, the build would
publish, and the regression would be found by a customer.

This turns the numbers into a check with a recorded expectation. It is
deliberately tolerant, because a CI runner is a noisy place to measure and a
gate that cries wolf gets disabled within a fortnight:

* Latency may rise by `--latency-tolerance` (default 50%) before it fails.
* Throughput may fall by `--throughput-tolerance` (default 30%).
* Comparison is on p95, not the mean: the mean hides a slow tail, and the tail
  is what a customer notices.

Usage
-----
    # record a baseline (do this on a quiet machine, deliberately)
    python scripts/perf_gate.py --results results.json --update-baseline

    # check a run against it
    python scripts/perf_gate.py --results results.json

Exit codes: 0 pass, 1 regression, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_BASELINE = Path(__file__).resolve().parents[1] / "deploy" / "perf-baseline.json"


def load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    except FileNotFoundError:
        print(f"error: {path} does not exist", file=sys.stderr)
        raise SystemExit(2)
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(2)


def compare(results: dict[str, Any], baseline: dict[str, Any], *, latency_tolerance: float, throughput_tolerance: float) -> list[str]:
    """Return the regressions found. Empty means the run passed."""
    failures: list[str] = []

    current_p95 = (results.get("latency_ms") or {}).get("p95")
    baseline_p95 = (baseline.get("latency_ms") or {}).get("p95")
    if current_p95 is not None and baseline_p95:
        ceiling = baseline_p95 * (1 + latency_tolerance)
        marker = "FAIL" if current_p95 > ceiling else "ok"
        print(
            f"  [{marker}] p95 latency {current_p95:.1f}ms "
            f"(baseline {baseline_p95:.1f}ms, ceiling {ceiling:.1f}ms)"
        )
        if current_p95 > ceiling:
            failures.append(
                f"p95 latency regressed: {current_p95:.1f}ms vs a "
                f"{ceiling:.1f}ms ceiling ({baseline_p95:.1f}ms baseline "
                f"+{latency_tolerance:.0%})"
            )

    current_throughput = results.get("logs_per_second")
    baseline_throughput = baseline.get("logs_per_second")
    if current_throughput is not None and baseline_throughput:
        floor = baseline_throughput * (1 - throughput_tolerance)
        marker = "FAIL" if current_throughput < floor else "ok"
        print(
            f"  [{marker}] throughput {current_throughput:,.0f} logs/s "
            f"(baseline {baseline_throughput:,.0f}, floor {floor:,.0f})"
        )
        if current_throughput < floor:
            failures.append(
                f"throughput regressed: {current_throughput:,.0f} logs/s vs a "
                f"{floor:,.0f} floor ({baseline_throughput:,.0f} baseline "
                f"-{throughput_tolerance:.0%})"
            )

    # An error under load is not a performance question, it is a correctness
    # one, and no tolerance applies to it.
    errors = int(results.get("requests_err", 0)) + int(results.get("requests_exc", 0))
    total = errors + int(results.get("requests_ok", 0))
    if total and errors / total > 0.01:
        failures.append(
            f"{errors}/{total} requests failed under load ({errors / total:.1%}); "
            "the run is not a valid performance measurement"
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Performance regression gate")
    parser.add_argument("--results", required=True, type=Path, help="loadtest.py --json output")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--latency-tolerance", type=float, default=0.5)
    parser.add_argument("--throughput-tolerance", type=float, default=0.3)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="record this run as the new baseline instead of checking against one",
    )
    args = parser.parse_args()

    results = load(args.results)

    if args.update_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(results, indent=2) + "\n")
        print(f"Baseline recorded at {args.baseline}")
        return 0

    if not args.baseline.exists():
        # Not a failure. The first run on a new machine has nothing to compare
        # against, and failing here would make the gate impossible to adopt.
        print(
            f"No baseline at {args.baseline}; nothing to compare against.\n"
            "Record one with --update-baseline once you trust a run."
        )
        return 0

    baseline = load(args.baseline)
    print("Performance gate")
    failures = compare(
        results,
        baseline,
        latency_tolerance=args.latency_tolerance,
        throughput_tolerance=args.throughput_tolerance,
    )

    if failures:
        print("\nRegressions:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("\nNo regression.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
