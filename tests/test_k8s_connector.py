"""Kubernetes connector: pod discovery, timestamped log parsing, level
inference, the polling collector, and pipeline compatibility — all against an
injected fake Kubernetes API (no cluster required)."""

from types import SimpleNamespace

from denoiser.integrations.k8s import KubernetesLogCollector, KubernetesReader
from denoiser.storage.clickhouse_store import resolve_level, resolve_source, resolve_timestamp


def _pod(name, ns, phase, ip, containers):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=ns),
        status=SimpleNamespace(phase=phase, pod_ip=ip),
        spec=SimpleNamespace(containers=[SimpleNamespace(name=c) for c in containers]),
    )


class FakeCoreV1Api:
    def __init__(self, pods, logs):
        self.pods = pods
        self.logs = logs  # {(ns, name, container): "raw log text"}

    def list_namespaced_pod(self, namespace, label_selector=None):
        return SimpleNamespace(items=[p for p in self.pods if p.metadata.namespace == namespace])

    def list_pod_for_all_namespaces(self, label_selector=None):
        return SimpleNamespace(items=list(self.pods))

    def read_namespaced_pod_log(self, name, namespace, container=None, timestamps=False,
                                tail_lines=None, since_seconds=None):
        return self.logs.get((namespace, name, container), "")


ERR_LOG = (
    "2024-06-01T12:00:00.123456789Z ERROR database connection refused\n"
    "2024-06-01T12:00:01Z request handled ok\n"
)


def _reader():
    pods = [
        _pod("web-abc", "prod", "Running", "10.0.0.1", ["app"]),
        _pod("db-xyz", "prod", "Running", "10.0.0.2", ["postgres"]),
        _pod("cron-1", "infra", "Failed", "10.0.1.9", ["job"]),
    ]
    logs = {
        ("prod", "web-abc", "app"): ERR_LOG,
        ("prod", "db-xyz", "postgres"): "2024-06-01T12:00:05Z WARN slow query\n",
    }
    return KubernetesReader(api=FakeCoreV1Api(pods, logs))


class TestDiscovery:
    def test_list_all_pods(self):
        pods = _reader().list_pods()
        assert len(pods) == 3
        assert {p["namespace"] for p in pods} == {"prod", "infra"}
        assert pods[0]["containers"] == ["app"]

    def test_list_namespaced(self):
        pods = _reader().list_pods(namespace="prod")
        assert {p["name"] for p in pods} == {"web-abc", "db-xyz"}


class TestLogReading:
    def test_parses_timestamps_and_level(self):
        recs = _reader().read_records("prod", "web-abc", container="app")
        assert len(recs) == 2
        assert recs[0]["level"] == "ERROR"
        assert recs[0]["message"] == "ERROR database connection refused"
        assert recs[0]["source"] == "app"
        assert recs[0]["pod"] == "web-abc"
        assert recs[0]["namespace"] == "prod"
        assert "timestamp" in recs[0]
        assert recs[1]["level"] == "INFO"

    def test_pipeline_resolvers_agree(self):
        rec = _reader().read_records("prod", "web-abc", container="app")[0]
        assert resolve_source(rec) == "app"
        assert resolve_level(rec) == "ERROR"
        assert resolve_timestamp(rec).year == 2024


class TestCollector:
    def test_collect_once_feeds_sink(self):
        received: list[dict] = []
        collector = KubernetesLogCollector(
            _reader(), sink=lambda recs, t: received.extend(recs), tenant_id="7"
        )
        count = collector.collect_once(namespace="prod")
        # web-abc (2 lines) + db-xyz (1 line)
        assert count == 3
        assert len(received) == 3
        assert any(r["level"] == "ERROR" for r in received)
        assert any(r["level"] == "WARN" for r in received)

    def test_tenant_passed_to_sink(self):
        seen = {}
        collector = KubernetesLogCollector(
            _reader(), sink=lambda recs, t: seen.update({"tenant": t}), tenant_id="7"
        )
        collector.collect_once(namespace="prod")
        assert seen["tenant"] == "7"

    def test_read_failure_is_isolated(self):
        # A pod whose logs raise must not abort the whole pass.
        class FlakyApi(FakeCoreV1Api):
            def read_namespaced_pod_log(self, name, namespace, **kw):
                if name == "db-xyz":
                    raise RuntimeError("forbidden")
                return super().read_namespaced_pod_log(name, namespace, **kw)

        reader = _reader()
        reader.api = FlakyApi(reader.api.pods, reader.api.logs)
        received: list[dict] = []
        collector = KubernetesLogCollector(reader, sink=lambda recs, t: received.extend(recs))
        count = collector.collect_once(namespace="prod")
        assert count == 2  # web-abc's lines survived; db-xyz failed but didn't crash
