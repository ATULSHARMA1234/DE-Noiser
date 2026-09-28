"""
Kubernetes log connector.

Discovers pods and reads their logs directly from the Kubernetes API — with
timestamps — and normalizes each line into the standard log-record shape the
pipeline consumes. Works both in-cluster (ServiceAccount) and from a local
kubeconfig. A poll-based collector streams recent logs from a selection of pods
into the ingestion sink continuously.

The Kubernetes client is imported lazily and the API object is injectable, so
this module can be imported and unit-tested without the ``kubernetes`` package
or a live cluster.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from denoiser.exceptions import IngestionError
from denoiser.ingestion.models import LogRecord
from denoiser.logging import get_logger

logger = get_logger(__name__)


def _parse_rfc3339(s: str) -> datetime | None:
    """Parse a Kubernetes log timestamp (RFC3339, nanosecond precision)."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # datetime.fromisoformat accepts at most microseconds; drop extra nanos.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _infer_level(message: str) -> str:
    upper = message.upper()
    if "FATAL" in upper or "CRITICAL" in upper or "PANIC" in upper:
        return "FATAL"
    if "ERROR" in upper or " ERR " in upper:
        return "ERROR"
    if "WARN" in upper:
        return "WARN"
    if "DEBUG" in upper or "TRACE" in upper:
        return "DEBUG"
    return "INFO"


def _split_timestamp(line: str) -> tuple[int | None, str]:
    """Split a `timestamps=True` k8s log line into (epoch_ms, message)."""
    parts = line.split(" ", 1)
    if len(parts) == 2:
        dt = _parse_rfc3339(parts[0])
        if dt is not None:
            return int(dt.timestamp() * 1000), parts[1]
    return None, line


class KubernetesReader:
    """Reads logs and lists pods from the Kubernetes API."""

    def __init__(self, api: Any | None = None) -> None:
        self.api = api if api is not None else self._load_default_api()

    @staticmethod
    def _load_default_api() -> Any:
        from kubernetes import client, config

        try:
            config.load_incluster_config()  # running as a pod
            logger.debug("Loaded in-cluster Kubernetes config.")
        except Exception:
            try:
                config.load_kube_config()  # local kubeconfig
                logger.debug("Loaded local kubeconfig.")
            except Exception as e:
                raise IngestionError(f"Failed to load Kubernetes config: {e}") from e
        return client.CoreV1Api()

    def list_pods(self, namespace: str | None = None, label_selector: str | None = None) -> list[dict[str, Any]]:
        if namespace:
            resp = self.api.list_namespaced_pod(namespace, label_selector=label_selector)
        else:
            resp = self.api.list_pod_for_all_namespaces(label_selector=label_selector)
        pods: list[dict[str, Any]] = []
        for p in resp.items:
            containers = [c.name for c in (getattr(p.spec, "containers", None) or [])]
            pods.append({
                "name": p.metadata.name,
                "namespace": p.metadata.namespace,
                "status": getattr(p.status, "phase", None),
                "ip": getattr(p.status, "pod_ip", None),
                "containers": containers,
            })
        return pods

    def read_records(
        self,
        namespace: str,
        pod_name: str,
        container: str | None = None,
        tail_lines: int | None = None,
        since_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read a pod's logs (with timestamps) as normalized log records."""
        raw = self.api.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            timestamps=True,
            tail_lines=tail_lines,
            since_seconds=since_seconds,
        )
        return list(self._to_records(raw, namespace, pod_name, container))

    def _to_records(self, raw: str, namespace: str, pod_name: str, container: str | None) -> Iterator[dict[str, Any]]:
        for line in (raw or "").splitlines():
            if not line.strip():
                continue
            ts, message = _split_timestamp(line)
            record: dict[str, Any] = {
                "source": container or pod_name,
                "namespace": namespace,
                "pod": pod_name,
                "message": message,
                "level": _infer_level(message),
                "source_protocol": "kubernetes",
            }
            if container:
                record["container"] = container
            if ts is not None:
                record["timestamp"] = ts
            yield record

    def read(self, namespace: str, pod_name: str) -> Iterator[LogRecord]:
        """Backward-compatible LogRecord stream (used by the fetch-to-file path)."""
        source_name = f"k8s://{namespace}/{pod_name}"
        for i, rec in enumerate(self.read_records(namespace, pod_name), 1):
            yield LogRecord(
                raw_text=rec["message"],
                source=source_name,
                line_number=i,
                metadata={k: v for k, v in rec.items() if k not in ("message",)},
            )


# Sink: (records, tenant_id) -> None. Injectable for tests.
Sink = Callable[[list[dict[str, Any]], str], None]


class KubernetesLogCollector:
    """Polls a selection of pods and streams their recent logs into a sink."""

    def __init__(self, reader: KubernetesReader, sink: Sink, tenant_id: str = "default_tenant") -> None:
        self.reader = reader
        self.sink = sink
        self.tenant_id = tenant_id

    def collect_once(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
        tail_lines: int | None = 200,
        since_seconds: int | None = None,
    ) -> int:
        """One discovery + read pass over all matching pods/containers."""
        pods = self.reader.list_pods(namespace, label_selector)
        batch: list[dict[str, Any]] = []
        for pod in pods:
            containers = pod.get("containers") or [None]
            for container in containers:
                try:
                    batch.extend(self.reader.read_records(
                        pod["namespace"], pod["name"], container=container,
                        tail_lines=tail_lines, since_seconds=since_seconds,
                    ))
                except Exception as e:
                    logger.error(f"Failed to read logs for {pod['namespace']}/{pod['name']}/{container}: {e}")
        if batch:
            self.sink(batch, self.tenant_id)
        return len(batch)

    def run(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
        interval_seconds: int = 30,
    ) -> None:
        """Poll forever, reading only logs produced since the previous poll (so
        lines are not re-ingested)."""
        # First pass seeds from a short tail; subsequent passes use since_seconds.
        since: int | None = None
        while True:
            try:
                count = self.collect_once(
                    namespace=namespace,
                    label_selector=label_selector,
                    tail_lines=None if since else 100,
                    since_seconds=since,
                )
                logger.info(f"k8s collector: ingested {count} lines")
            except Exception as e:
                logger.error(f"k8s collector poll failed: {e}")
            since = interval_seconds + 5  # small overlap to avoid gaps
            time.sleep(interval_seconds)
