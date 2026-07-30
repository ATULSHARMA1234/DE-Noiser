"""The process-wide handles: one owner, no cycle, nothing opened at import.

`denoiser.api.main` used to own `redis_client`, `clickhouse_store`,
`kafka_producer` and `DATA_DIR`, which made it the container everything else had
to reach into. Four modules imported back from it — including
`denoiser.storage.archiver`, which had to import the whole HTTP application to
obtain a settings function — and every one of those imports had to sit *inside a
function body*, because at module scope it was a cycle.

That is 114 deferred imports across the API package, most of them working around
this one fact. It also meant importing a router pulled in the app, which opened a
Redis connection and issued two ClickHouse `CREATE TABLE` statements as a side
effect of an import — which is why the test suite has a conftest that must run
before anything under `denoiser` is imported, and asserts afterwards that it did.

Everything here is built on **first use**, not at import. Importing this module
opens no sockets and creates no tables, so a module that needs a handle can say
so at the top of the file like any other dependency.

Substitution is a single seam: `set_clickhouse_store(FakeStore())` in a fixture
replaces it for every caller at once, where previously each of the three
independently-constructed ClickHouse clients had to be patched by module path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from denoiser.logging import get_logger

logger = get_logger(__name__)

_redis_client: Any = None
_clickhouse_store: Any = None
_kafka_producer: Any = None
_data_dir: Path | None = None


# ── Log data directory ───────────────────────────────────────────────────────

def data_dir() -> Path:
    """Where uploaded sources, run artifacts and the dead-letter queue live.

    Read through a function rather than bound at import so that a test — or a
    deployment that sets the variable late — is not racing module import order
    to change it.
    """
    global _data_dir
    if _data_dir is None:
        _data_dir = Path(os.getenv("SEMANTICOS_DATA_DIR", "data"))
    return _data_dir


# ── Redis ────────────────────────────────────────────────────────────────────

def redis_client() -> Any:
    """The shared async Redis client, connected lazily."""
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as redis_asyncio

        _redis_client = redis_asyncio.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
    return _redis_client


def set_redis_client(client: Any) -> None:
    """Replace the shared client. The seam tests substitute through."""
    global _redis_client
    _redis_client = client


# ── ClickHouse ───────────────────────────────────────────────────────────────

def clickhouse_store() -> Any:
    """The shared ClickHouse store.

    One instance, not the thirteen that were being constructed across the
    codebase — each of which re-ran `_init_client` and re-issued both
    `CREATE TABLE IF NOT EXISTS` statements. Two of those were on scheduled
    paths, so the schema DDL ran once a minute for no reason.
    """
    global _clickhouse_store
    if _clickhouse_store is None:
        from denoiser.storage.clickhouse_store import ClickHouseStore

        _clickhouse_store = ClickHouseStore()
    return _clickhouse_store


def set_clickhouse_store(store: Any) -> None:
    global _clickhouse_store
    _clickhouse_store = store


# ── Kafka ────────────────────────────────────────────────────────────────────

def kafka_producer() -> Any:
    """The ingest producer, or None until the application lifespan starts it.

    Unlike the others this is not built on demand: an `AIOKafkaProducer` must be
    started inside the running event loop that will use it, so ownership stays
    with the lifespan and this is only the place it is published.
    """
    return _kafka_producer


def set_kafka_producer(producer: Any) -> None:
    global _kafka_producer
    _kafka_producer = producer


# ── Testing ──────────────────────────────────────────────────────────────────

def reset() -> None:
    """Drop every cached handle. For tests that change the environment."""
    global _redis_client, _clickhouse_store, _kafka_producer, _data_dir
    _redis_client = None
    _clickhouse_store = None
    _kafka_producer = None
    _data_dir = None
