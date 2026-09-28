"""Readiness must notice a missing ingestion consumer.

`POST /ingest` returns 200 once a record is handed to Kafka. With no consumer
running, the topic fills and nothing reaches ClickHouse — while readiness stayed
green, because it only ever checked the *producer*. Successful writes, a healthy
health check, and no queryable logs.
"""

import json
import time

import pytest

from denoiser.workers.heartbeat import (
    HEARTBEAT_KEY,
    evaluate_heartbeat,
    publish_heartbeat,
    read_heartbeat,
)


class FakeRedis:
    """Minimal async stand-in for the bits of redis the heartbeat uses."""

    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.raise_on_get = False
        self.raise_on_set = False

    async def set(self, key, value, ex=None):
        if self.raise_on_set:
            raise ConnectionError("redis down")
        self.store[key] = value

    async def get(self, key):
        if self.raise_on_get:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


class TestHeartbeatVerdict:
    def test_absent_consumer_fails_readiness(self):
        healthy, detail = evaluate_heartbeat(None)
        assert healthy is False
        assert "no ingestion consumer" in detail

    def test_fresh_heartbeat_passes(self):
        healthy, detail = evaluate_heartbeat({"at": time.time(), "lag": 0})
        assert healthy is True
        assert "ok" in detail

    def test_stale_heartbeat_fails(self, monkeypatch):
        monkeypatch.setenv("INGESTION_HEARTBEAT_STALE_SECONDS", "60")
        healthy, detail = evaluate_heartbeat({"at": time.time() - 300, "lag": 0})
        assert healthy is False
        assert "stalled or stopped" in detail

    def test_lag_beyond_the_ceiling_fails(self, monkeypatch):
        monkeypatch.setenv("INGESTION_LAG_CRITICAL", "1000")
        healthy, detail = evaluate_heartbeat({"at": time.time(), "lag": 50_000})
        assert healthy is False
        assert "50000 records behind" in detail

    def test_lag_within_the_ceiling_passes(self, monkeypatch):
        monkeypatch.setenv("INGESTION_LAG_CRITICAL", "1000")
        healthy, detail = evaluate_heartbeat({"at": time.time(), "lag": 500})
        assert healthy is True
        assert "lag 500" in detail

    def test_unknown_lag_is_not_treated_as_caught_up(self):
        """A consumer with no assignment yet reports None, never 0."""
        healthy, detail = evaluate_heartbeat({"at": time.time(), "lag": None})
        assert healthy is True
        assert "lag" not in detail


class TestHeartbeatTransport:
    @pytest.mark.asyncio
    async def test_publish_then_read_round_trip(self):
        redis = FakeRedis()
        await publish_heartbeat(redis, lag=42, assigned=3)

        stored = json.loads(redis.store[HEARTBEAT_KEY])
        assert stored["lag"] == 42
        assert stored["assigned_partitions"] == 3

        assert (await read_heartbeat(redis))["lag"] == 42

    @pytest.mark.asyncio
    async def test_publish_never_raises_on_a_redis_outage(self):
        """Ingestion is the one thing still working; do not kill it over a heartbeat."""
        redis = FakeRedis()
        redis.raise_on_set = True
        await publish_heartbeat(redis, lag=0, assigned=1)  # must not raise

    @pytest.mark.asyncio
    async def test_unreadable_heartbeat_is_treated_as_absent(self):
        redis = FakeRedis()
        redis.raise_on_get = True
        assert await read_heartbeat(redis) is None
        assert evaluate_heartbeat(await read_heartbeat(redis))[0] is False

    @pytest.mark.asyncio
    async def test_corrupt_heartbeat_is_treated_as_absent(self):
        redis = FakeRedis({HEARTBEAT_KEY: "not json"})
        assert await read_heartbeat(redis) is None


class TestReadinessIntegration:
    def test_consumer_is_not_required_without_a_producer(self):
        """Without Kafka, /ingest writes straight to ClickHouse — no consumer in the path."""
        from fastapi.testclient import TestClient

        import denoiser.api.main as main
        from denoiser import runtime

        with TestClient(main.app) as client:
            # TestClient startup leaves the producer unset when no broker is up.
            if runtime.kafka_producer() is None:
                body = client.get("/health/ready").json()
                assert body["checks"]["ingestion_consumer"].startswith("not_required")

    def test_missing_consumer_makes_readiness_degraded(self, monkeypatch):
        from fastapi.testclient import TestClient

        import denoiser.api.main as main
        from denoiser import runtime

        class StubProducer:
            async def stop(self):
                pass

        with TestClient(main.app) as client:
            # Published through the runtime seam rather than set on the module,
            # so every reader sees it — there is now only one reader.
            runtime.set_kafka_producer(StubProducer())
            monkeypatch.setattr(runtime, "_kafka_producer", StubProducer())

            async def _no_heartbeat(_redis):
                return None

            monkeypatch.setattr(
                "denoiser.workers.heartbeat.read_heartbeat", _no_heartbeat
            )

            res = client.get("/health/ready")
            body = res.json()
            assert "no ingestion consumer" in body["checks"]["ingestion_consumer"]
            assert res.status_code == 503
            assert body["status"] == "degraded"
