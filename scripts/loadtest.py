#!/usr/bin/env python3
"""
Ingestion load-test harness for SemanticOS.

The README positions SemanticOS as a high-throughput platform, but nothing
measured it. This script hammers the ``/ingest`` endpoint with concurrent
workers sending batched logs and reports achieved throughput and latency
percentiles, so the throughput claim becomes something you can actually check on
your own hardware.

Usage:
    python scripts/loadtest.py --url http://localhost:8000 \
        --api-key "$INGEST_API_KEY" --concurrency 16 --duration 30 --batch 200

Auth: pass --api-key (X-API-Key) or --token (Bearer JWT).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

_SAMPLE_LEVELS = ("INFO", "INFO", "INFO", "WARN", "ERROR")


def _make_batch(n: int, worker: int, seq: int) -> list[dict]:
    now_ms = int(time.time() * 1000)
    return [
        {
            "timestamp": now_ms,
            "level": _SAMPLE_LEVELS[(worker + seq + i) % len(_SAMPLE_LEVELS)],
            "service": f"loadtest-svc-{worker % 4}",
            "message": f"synthetic log line w{worker} s{seq} i{i}",
        }
        for i in range(n)
    ]


async def _worker(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    batch: int,
    deadline: float,
    worker_id: int,
    latencies: list[float],
    counters: dict[str, int],
) -> None:
    seq = 0
    while time.perf_counter() < deadline:
        payload = {"logs": _make_batch(batch, worker_id, seq)}
        seq += 1
        start = time.perf_counter()
        try:
            resp = await client.post(f"{url}/ingest", json=payload, headers=headers)
            elapsed = time.perf_counter() - start
            latencies.append(elapsed)
            if resp.status_code == 200:
                counters["ok"] += 1
                counters["logs"] += batch
            else:
                counters["err"] += 1
        except Exception:
            counters["exc"] += 1


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(p / 100 * len(ordered)))
    return ordered[idx]


async def main() -> None:
    ap = argparse.ArgumentParser(description="SemanticOS /ingest load test")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--duration", type=float, default=30.0, help="seconds")
    ap.add_argument("--batch", type=int, default=200, help="logs per request")
    ap.add_argument(
        "--json",
        dest="json_path",
        default=None,
        help="also write the results as JSON here, for the CI performance gate",
    )
    args = ap.parse_args()

    headers: dict[str, str] = {}
    if args.api_key:
        headers["X-API-Key"] = args.api_key
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    if not headers:
        raise SystemExit("Provide --api-key or --token for authenticated ingest.")

    latencies: list[float] = []
    counters: dict[str, int] = {"ok": 0, "err": 0, "exc": 0, "logs": 0}

    print(
        f"Load test: {args.concurrency} workers x {args.duration}s, "
        f"{args.batch} logs/request -> {args.url}/ingest"
    )
    deadline = time.perf_counter() + args.duration
    limits = httpx.Limits(max_connections=args.concurrency * 2)
    async with httpx.AsyncClient(timeout=30, limits=limits) as client:
        wall_start = time.perf_counter()
        await asyncio.gather(*[
            _worker(client, args.url, headers, args.batch, deadline, w, latencies, counters)
            for w in range(args.concurrency)
        ])
        wall = time.perf_counter() - wall_start

    total_reqs = counters["ok"] + counters["err"]
    print("\n── Results ─────────────────────────────")
    print(f"  wall time         : {wall:.1f}s")
    print(f"  requests ok/err/exc: {counters['ok']}/{counters['err']}/{counters['exc']}")
    print(f"  logs ingested     : {counters['logs']:,}")
    print(f"  throughput        : {counters['logs'] / wall:,.0f} logs/s "
          f"({total_reqs / wall:,.0f} req/s)")
    if latencies:
        print(f"  latency p50/p95/p99: "
              f"{_pct(latencies, 50)*1000:.1f} / "
              f"{_pct(latencies, 95)*1000:.1f} / "
              f"{_pct(latencies, 99)*1000:.1f} ms "
              f"(mean {statistics.mean(latencies)*1000:.1f} ms)")

    if args.json_path:
        # Machine-readable, so a CI gate can compare against a recorded
        # baseline instead of somebody reading the numbers above and
        # remembering what they used to be.
        results = {
            "wall_seconds": round(wall, 3),
            "requests_ok": counters["ok"],
            "requests_err": counters["err"],
            "requests_exc": counters["exc"],
            "logs_ingested": counters["logs"],
            "logs_per_second": round(counters["logs"] / wall, 1) if wall else 0.0,
            "requests_per_second": round(total_reqs / wall, 1) if wall else 0.0,
            "latency_ms": {
                "p50": round(_pct(latencies, 50) * 1000, 2) if latencies else None,
                "p95": round(_pct(latencies, 95) * 1000, 2) if latencies else None,
                "p99": round(_pct(latencies, 99) * 1000, 2) if latencies else None,
            },
            "config": {
                "concurrency": args.concurrency,
                "duration": args.duration,
                "batch": args.batch,
            },
        }
        Path(args.json_path).write_text(json.dumps(results, indent=2) + "\n")
        print(f"\n  results written to {args.json_path}")


if __name__ == "__main__":
    asyncio.run(main())
