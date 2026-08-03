"""What has to hold before the API can run on more than one replica.

Each test below is a reproduction of a defect that was invisible at one replica
and silently wrong at two. They are grouped by the state that used to be
process-local:

  * the raw-log copy and uploaded sources, which lived on one pod's disk
  * the SAML assertion replay guard, which lived in one worker's memory
  * the ingestion dead-letter queue, which lived on one worker pod's disk
  * the metrics endpoint, which had no authentication at all

The shared-storage tests use in-memory fakes rather than MinIO: the contract
being asserted is "the second process can see what the first wrote", and a real
object store adds a dependency without adding evidence.
"""

from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path

import pytest

from denoiser import runtime

# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeS3:
    """Enough of the boto3 S3 surface for the two stores, backed by a dict.

    Shared by construction: handing the same instance to two store objects is
    exactly the "two replicas, one bucket" situation under test.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_put = False

    def put_object(self, Bucket, Key, Body, **kwargs):  # noqa: N803 - boto3 wire names
        if self.fail_put:
            raise OSError("bucket unreachable")
        self.objects[Key] = Body

    def upload_fileobj(self, fileobj, bucket, key):
        if self.fail_put:
            raise OSError("bucket unreachable")
        self.objects[key] = fileobj.read()

    def download_fileobj(self, bucket, key, fileobj):
        if key not in self.objects:
            raise KeyError(key)
        fileobj.write(self.objects[key])

    def delete_object(self, Bucket, Key):  # noqa: N803 - boto3 wire names
        self.objects.pop(Key, None)

    def get_paginator(self, _name):
        outer = self

        class _Paginator:
            def paginate(self, Bucket, Prefix):  # noqa: N803 - boto3 wire names
                contents = [
                    {"Key": k, "Size": len(v), "LastModified": None}
                    for k, v in outer.objects.items()
                    if k.startswith(Prefix)
                ]
                return [{"Contents": contents}]

        return _Paginator()


class FakeRedis:
    """A single shared Redis, so two 'processes' contend on the same keys."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, int]] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    def scan_iter(self, match=None):
        return iter(list(self.strings))

    def delete(self, key):
        self.strings.pop(key, None)


# ── The raw ingest copy ──────────────────────────────────────────────────────

class TestRawLogCopyIsShared:
    def test_two_processes_writing_one_bucket_do_not_overwrite_each_other(self):
        """The key carries the writing instance, so replicas cannot collide.

        With a shared filename per batch, the second replica's PUT would land on
        the first's key and one batch would vanish with no error anywhere.
        """
        from denoiser.storage.raw_log_sink import ObjectStoreRawLogSink

        bucket = FakeS3()
        replica_a = ObjectStoreRawLogSink(bucket, "logs")
        replica_b = ObjectStoreRawLogSink(bucket, "logs")

        replica_a.write("7", ['{"message": "from A"}'])
        replica_b.write("7", ['{"message": "from B"}'])

        assert len(bucket.objects) == 2

        payloads = set()
        for body in bucket.objects.values():
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
                payloads.add(gz.read().decode().strip())
        assert payloads == {'{"message": "from A"}', '{"message": "from B"}'}

    def test_batches_are_partitioned_by_tenant(self):
        """One customer's raw logs must be locatable — and deletable — alone."""
        from denoiser.storage.raw_log_sink import ObjectStoreRawLogSink

        bucket = FakeS3()
        sink = ObjectStoreRawLogSink(bucket, "logs")
        sink.write("1", ["a"])
        sink.write("2", ["b"])

        assert all(k.startswith("raw/tenant=") for k in bucket.objects)
        assert len({k.split("/")[1] for k in bucket.objects}) == 2

    def test_a_bucket_failure_does_not_fail_the_ingest(self):
        """The copy is redundant; Kafka and ClickHouse carry the real record.

        Raising here would trade a durable pipeline for a convenience one.
        """
        from denoiser.storage.raw_log_sink import ObjectStoreRawLogSink

        bucket = FakeS3()
        bucket.fail_put = True
        ObjectStoreRawLogSink(bucket, "logs").write("1", ["a"])  # must not raise

    def test_the_archive_bucket_default_does_not_silently_enable_object_storage(self):
        """`s3_bucket` defaults to a value on every install.

        Inheriting it here would have routed every deployment's ingest through
        an object store it may not be running.
        """
        from denoiser.storage.raw_log_sink import LocalFileRawLogSink, build_raw_log_sink

        sink = build_raw_log_sink(settings={"s3_bucket": "semanticos-logs"}, data_dir=Path("data"))
        assert isinstance(sink, LocalFileRawLogSink)

    def test_a_multi_replica_deployment_refuses_a_local_only_copy(self, monkeypatch):
        """On several replicas a local file is not degraded, it is split."""
        from denoiser.storage.raw_log_sink import build_raw_log_sink

        monkeypatch.setenv("SEMANTICOS_MULTI_REPLICA", "1")
        with pytest.raises(RuntimeError, match="raw-log bucket"):
            build_raw_log_sink(settings={}, data_dir=Path("data"))


# ── Uploaded sources ─────────────────────────────────────────────────────────

class TestUploadedSourcesAreShared:
    def test_a_replica_that_never_saw_the_upload_can_still_read_it(self, tmp_path):
        """The defect: upload hits pod A, /analyze routes to pod B, 404.

        Hydration is what makes the two pods interchangeable.
        """
        from denoiser.storage.source_store import ObjectSourceStore

        bucket = FakeS3()
        uploader = ObjectSourceStore(bucket, "sources")

        original = tmp_path / "app.log"
        original.write_text("ERROR boom\n")
        uploader.put(3, "app.log", original)

        # A different pod: nothing on disk, same bucket.
        other_pod_dir = tmp_path / "other-pod"
        other_pod_dir.mkdir()
        destination = other_pod_dir / "app.log"

        assert ObjectSourceStore(bucket, "sources").fetch(3, "app.log", destination)
        assert destination.read_text() == "ERROR boom\n"

    def test_listing_is_scoped_to_the_asking_tenant(self):
        from denoiser.storage.source_store import ObjectSourceStore

        bucket = FakeS3()
        store = ObjectSourceStore(bucket, "sources")
        bucket.objects["sources/tenant=1/mine.log"] = b"x"
        bucket.objects["sources/tenant=2/theirs.log"] = b"y"

        assert [s.name for s in store.list(1)] == ["mine.log"]
        assert [s.name for s in store.list(2)] == ["theirs.log"]

    def test_hydration_cannot_be_used_to_traverse(self, tmp_path, monkeypatch):
        """Hydration must not become a second, weaker path to a file.

        It only ever materialises a bare filename inside the caller's own
        tenant directory, so it can produce nothing the caller was not already
        entitled to.
        """
        import denoiser.api.sources as sources

        monkeypatch.setattr(sources, "DATA_DIR", tmp_path)

        fetched: list[tuple] = []

        class RecordingStore:
            def enabled(self):
                return True

            def fetch(self, tenant_id, name, dest):
                fetched.append((tenant_id, name, dest))
                return False

        runtime.set_source_store(RecordingStore())
        try:
            sources.hydrate("../../etc/passwd", 5)
        finally:
            runtime.set_source_store(None)

        assert len(fetched) == 1
        _tenant, name, dest = fetched[0]
        assert name == "passwd"
        assert dest.parent == sources.tenant_dir(5)

    def test_the_archive_bucket_default_does_not_silently_enable_object_storage(self):
        from denoiser.storage.source_store import NullSourceStore, build_source_store

        store = build_source_store(settings={"s3_bucket": "semanticos-logs"})
        assert isinstance(store, NullSourceStore)

    def test_a_multi_replica_deployment_refuses_local_only_uploads(self, monkeypatch):
        from denoiser.storage.source_store import build_source_store

        monkeypatch.setenv("SEMANTICOS_MULTI_REPLICA", "1")
        with pytest.raises(RuntimeError, match="source bucket"):
            build_source_store(settings={})


# ── SAML assertion replay ────────────────────────────────────────────────────

class TestAssertionReplayGuardIsShared:
    def test_a_second_process_cannot_spend_the_same_assertion(self):
        """The image runs uvicorn with several workers.

        A process-local guard let one assertion be spent once in each of them —
        four times per pod, not once per pod as the old docstring claimed.
        """
        import time

        from denoiser.api.saml import _AssertionReplayGuard, set_replay_redis

        shared = FakeRedis()
        set_replay_redis(shared)
        try:
            worker_one = _AssertionReplayGuard()
            worker_two = _AssertionReplayGuard()

            expires = time.time() + 300
            assert worker_one.claim("_assertion-id-1", expires) is True
            assert worker_two.claim("_assertion-id-1", expires) is False
        finally:
            set_replay_redis(None)

    def test_it_still_refuses_a_replay_when_redis_is_down(self):
        """Degraded, not disabled: within one process the guard still holds."""
        import time

        from denoiser.api.saml import _AssertionReplayGuard, set_replay_redis

        class DeadRedis:
            def set(self, *a, **k):
                raise ConnectionError("redis is down")

            def scan_iter(self, **k):
                raise ConnectionError("redis is down")

        set_replay_redis(DeadRedis())
        try:
            guard = _AssertionReplayGuard()
            expires = time.time() + 300
            assert guard.claim("_assertion-id-2", expires) is True
            assert guard.claim("_assertion-id-2", expires) is False
        finally:
            set_replay_redis(None)


# ── The dead-letter queue ────────────────────────────────────────────────────

class TestDeadLetterQueueSurvivesARestart:
    @pytest.mark.asyncio
    async def test_a_quarantined_record_goes_to_the_broker_not_local_disk(self):
        """A file on a pod with no volume is a delete, not a quarantine."""
        from denoiser.workers import dead_letter as dlq

        sent: list[tuple] = []

        class FakeProducer:
            async def send_and_wait(self, topic, value):
                sent.append((topic, value))

        await dlq.dead_letter("logs_topic", "unparseable", {"raw": "junk"}, producer=FakeProducer())

        assert len(sent) == 1
        topic, value = sent[0]
        assert topic == dlq.DLQ_TOPIC
        record = json.loads(value.decode())
        assert record["topic"] == "logs_topic"
        assert record["reason"] == "unparseable"
        assert record["payload"] == {"raw": "junk"}

    @pytest.mark.asyncio
    async def test_an_unreachable_broker_falls_back_to_the_file(self, tmp_path, monkeypatch):
        """Losing the record entirely is the one outcome not on the table."""
        from denoiser.workers import dead_letter as dlq

        fallback = tmp_path / "dlq.jsonl"
        monkeypatch.setattr(dlq, "DLQ_PATH", fallback)

        class DeadProducer:
            async def send_and_wait(self, topic, value):
                raise ConnectionError("no broker")

        await dlq.dead_letter("logs_topic", "flush failed", {"a": 1}, producer=DeadProducer())

        assert fallback.exists()
        assert json.loads(fallback.read_text().strip())["reason"] == "flush failed"

    @pytest.mark.asyncio
    async def test_every_quarantine_is_counted_so_it_can_be_alerted_on(self):
        """Silent loss with no series to alert on is what makes it dangerous."""
        from denoiser.workers import dead_letter as dlq

        class CountingRedis:
            def __init__(self):
                self.total = 0
                self.by_topic: dict[str, int] = {}

            async def incr(self, key):
                self.total += 1

            async def hincrby(self, key, field, amount):
                self.by_topic[field] = self.by_topic.get(field, 0) + amount

        class FakeProducer:
            async def send_and_wait(self, topic, value):
                return None

        counter = CountingRedis()
        for _ in range(3):
            await dlq.dead_letter(
                "logs_topic", "boom", {}, producer=FakeProducer(), redis_client=counter
            )

        assert counter.total == 3
        assert counter.by_topic == {"logs_topic": 3}

    def test_the_counters_render_as_prometheus_series(self):
        from denoiser.api.observability import render_dlq

        rendered = "\n".join(render_dlq({"total": 12, "by_topic": {"logs_topic": 12}}))
        assert "semanticos_ingestion_dead_lettered_total 12" in rendered
        assert 'topic="logs_topic"} 12' in rendered


# ── The metrics endpoint ─────────────────────────────────────────────────────

class TestMetricsScrapeIsAuthorized:
    def test_a_token_is_required_once_one_is_configured(self, monkeypatch):
        from fastapi import HTTPException

        from denoiser.api.observability import authorize_scrape

        monkeypatch.setenv("METRICS_TOKEN", "scrape-me")

        class Req:
            def __init__(self, header):
                self.headers = {"authorization": header} if header else {}

        with pytest.raises(HTTPException) as caught:
            authorize_scrape(Req(None))
        assert caught.value.status_code == 401

        with pytest.raises(HTTPException):
            authorize_scrape(Req("Bearer wrong"))

        authorize_scrape(Req("Bearer scrape-me"))  # must not raise

    def test_production_refuses_the_scrape_when_no_token_is_set(self, monkeypatch):
        """An operator who forgets the variable must not publish the route map."""
        from fastapi import HTTPException

        from denoiser.api import observability

        monkeypatch.delenv("METRICS_TOKEN", raising=False)

        class ProdSettings:
            is_production = True

        monkeypatch.setattr(observability, "get_settings", lambda: ProdSettings())

        class Req:
            headers: dict[str, str] = {}

        with pytest.raises(HTTPException) as caught:
            observability.authorize_scrape(Req())
        assert caught.value.status_code == 401
        assert "METRICS_TOKEN" in caught.value.detail

    def test_development_stays_open_so_curl_still_works(self, monkeypatch):
        from denoiser.api import observability

        monkeypatch.delenv("METRICS_TOKEN", raising=False)

        class DevSettings:
            is_production = False

        monkeypatch.setattr(observability, "get_settings", lambda: DevSettings())

        class Req:
            headers: dict[str, str] = {}

        observability.authorize_scrape(Req())  # must not raise


# ── Login does not stall the worker ──────────────────────────────────────────

class TestLoginDoesNotBlockTheEventLoop:
    def test_the_login_route_offloads_bcrypt_and_the_query(self):
        """`async def` + a sync Session + bcrypt stalls the whole worker.

        ~100ms of pure CPU per attempt, during which that process serves no
        health check, no ingest and no websocket traffic. Asserted on the source
        because the failure is structural: any test that merely calls the route
        passes either way.
        """
        import inspect

        from denoiser.api import main

        source = inspect.getsource(main.login)
        assert "run_in_threadpool" in source, (
            "login must run its blocking work off the event loop"
        )

    def test_an_unknown_address_still_costs_a_bcrypt_verify(self):
        """Otherwise the response time enumerates the user directory."""
        import inspect

        from denoiser.api import main

        source = inspect.getsource(main.login)
        assert "_DUMMY_PASSWORD_HASH" in source


# ── The chart no longer pins the API to one replica ──────────────────────────

class TestChartDefaults:
    def _values(self) -> dict:
        import yaml

        path = Path(__file__).resolve().parents[1] / "deploy/helm/semanticos/values.yaml"
        return yaml.safe_load(path.read_text())

    def test_the_api_defaults_to_more_than_one_replica(self):
        assert self._values()["api"]["replicaCount"] >= 2

    def test_a_rolling_update_never_drops_below_full_capacity(self):
        path = Path(__file__).resolve().parents[1] / "deploy/helm/semanticos/templates/api-deployment.yaml"
        rendered = path.read_text()
        assert "maxUnavailable: 0" in rendered

    def test_every_serving_component_has_a_disruption_budget(self):
        budgets = self._values()["podDisruptionBudget"]
        assert budgets["enabled"] is True
        for component in ("api", "web", "ingestion", "syslog"):
            assert budgets[component]["minAvailable"] >= 1, component

    def test_the_pdb_template_exists(self):
        path = Path(__file__).resolve().parents[1] / "deploy/helm/semanticos/templates/pdb.yaml"
        assert "kind: PodDisruptionBudget" in path.read_text()

    def test_a_network_policy_is_available(self):
        path = Path(__file__).resolve().parents[1] / "deploy/helm/semanticos/templates/networkpolicy.yaml"
        assert "kind: NetworkPolicy" in path.read_text()


@pytest.fixture(autouse=True)
def _reset_runtime_handles():
    """Keep a substituted store from leaking into the next test."""
    yield
    runtime.set_source_store(None)
    runtime.set_raw_log_sink(None)
    os.environ.pop("SEMANTICOS_MULTI_REPLICA", None)
