"""Where a tenant's logs can be pulled from — one interface, two adapters each.

The Kubernetes, AWS and Docker connectors were six route bodies in
`denoiser.api.main`, about two hundred lines, each with the same shape typed out
again: try the real client, catch everything, and — if the deployment allows it
— fabricate a plausible-looking sandbox response inline. The fake pod names and
fake log lines were literals inside the HTTP handler.

That had two costs. The real fetch was unreachable from a test, because the only
entry point was an HTTP route and the only branch a test could reliably take was
the simulated one — so the code that actually talks to Kubernetes had no
coverage at all. And "real or simulated" was a conditional in a handler rather
than a choice of implementation, so every new connector meant writing the
conditional again.

Real and simulated are both `Connector`s now. Choosing between them is
`connector()`, in one place, and the routes are three lines each.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from denoiser.logging import get_logger

logger = get_logger(__name__)


class ConnectorUnavailable(Exception):
    """The backend could not be reached and no substitute is permitted."""


def simulated_allowed() -> bool:
    """Whether a connector may answer with sandbox data when its backend is down.

    Simulated data is a dev/sandbox aid only. In production a connector that
    cannot reach its backend must return a real error, not fake data a buyer
    could mistake for real infrastructure.

    Outside production the fallback is on by default — that is what the README
    and the connector UI describe, and requiring an extra opt-in meant a fresh
    developer checkout answered every connector page with a 502. An explicit
    ALLOW_SIMULATED_CONNECTORS still wins in both directions, so production can
    opt in for a demo and a developer can opt out to rehearse the real failure.
    """
    from denoiser.settings import get_settings, is_testing

    explicit = os.getenv("ALLOW_SIMULATED_CONNECTORS")
    if explicit is not None:
        return explicit.lower() in ("1", "true", "yes")
    return is_testing() or not get_settings().is_production


@dataclass(frozen=True)
class Discovery:
    """What a connector can see: pods, log groups, containers."""

    items: list[dict]
    simulated: bool = False
    message: str | None = None

    def as_response(self, key: str) -> dict:
        payload: dict[str, Any] = {
            "status": "simulated" if self.simulated else "connected",
            key: self.items,
        }
        if self.message:
            payload["message"] = self.message
        return payload


@dataclass(frozen=True)
class Fetched:
    """Log lines pulled from a backend, before they are written anywhere."""

    lines: list[str]
    simulated: bool = False
    message: str | None = None


class Connector(Protocol):
    """A place logs come from.

    `unreachable` is what the caller catches: an adapter raises
    `ConnectorUnavailable` and the choice of what to do about it belongs to
    `connector()`, not to the adapter or the route.
    """

    #: Filename-safe identifier, and the prefix of the source file it writes.
    name: str

    def discover(self) -> Discovery: ...

    def fetch(self, **params: Any) -> Fetched: ...


# ── Kubernetes ───────────────────────────────────────────────────────────────

class KubernetesConnector:
    name = "k8s"

    def discover(self) -> Discovery:
        from denoiser.integrations.k8s import KubernetesReader

        try:
            return Discovery(items=KubernetesReader().list_pods()[:50])
        except Exception as e:
            raise ConnectorUnavailable(f"Kubernetes API not reachable: {e}") from e

    def fetch(self, *, namespace: str, pod_name: str, **_: Any) -> Fetched:
        from denoiser.integrations.k8s import KubernetesReader

        try:
            records = list(KubernetesReader().read(namespace, pod_name))
        except Exception as e:
            raise ConnectorUnavailable(f"Kubernetes API not reachable: {e}") from e
        return Fetched(lines=[r.raw_text for r in records])

    @staticmethod
    def source_name(*, namespace: str, pod_name: str, **_: Any) -> str:
        return f"k8s_{namespace}_{pod_name}.log"


class SimulatedKubernetes:
    name = "k8s"
    _MESSAGE = "Local kubeconfig not detected. Operating in high-fidelity sandbox mode."

    def discover(self) -> Discovery:
        return Discovery(
            simulated=True,
            message=self._MESSAGE,
            items=[
                {"name": "auth-service-7f98c6", "namespace": "prod", "status": "Running", "ip": "10.244.0.12"},
                {"name": "payment-api-5b92d4", "namespace": "prod", "status": "Running", "ip": "10.244.0.15"},
                {"name": "ingress-nginx-controller-8a2b", "namespace": "ingress", "status": "Running", "ip": "10.244.1.2"},
                {"name": "db-backup-cron-9231", "namespace": "infra", "status": "Failed", "ip": "10.244.2.40"},
                {"name": "frontend-dashboard-f281", "namespace": "prod", "status": "Pending", "ip": "10.244.0.18"},
            ],
        )

    def fetch(self, *, pod_name: str, **_: Any) -> Fetched:
        return Fetched(
            simulated=True,
            message="Local kubeconfig not detected. Generated sandbox log sequence.",
            lines=[
                f"2026-05-17T17:15:00Z [INFO] [{pod_name}] Starting bootstrap process...",
                f"2026-05-17T17:15:02Z [INFO] [{pod_name}] Loaded active configuration schema version 4.2.1",
                f"2026-05-17T17:15:05Z [WARNING] [{pod_name}] Slow connection detected to database replication secondary",
                f"2026-05-17T17:15:07Z [ERROR] [{pod_name}] Timeout accessing authentication microservice endpoint /verify",
                f"2026-05-17T17:15:10Z [FATAL] [{pod_name}] Process terminated unexpectedly: OutOfMemoryException (OOMKilled)",
            ],
        )


# ── AWS CloudWatch ───────────────────────────────────────────────────────────

class AwsConnector:
    name = "aws"

    def discover(self) -> Discovery:
        from denoiser.integrations.aws import build_logs_client

        try:
            groups = build_logs_client().describe_log_groups(limit=50)
        except Exception as e:
            raise ConnectorUnavailable(f"AWS CloudWatch not reachable: {e}") from e
        return Discovery(items=[
            {
                "name": g["logGroupName"],
                "arn": g["arn"],
                "stored_bytes": g.get("storedBytes", 0),
            }
            for g in groups.get("logGroups", [])
        ])

    def fetch(self, *, log_group: str, log_stream: str | None = None, **_: Any) -> Fetched:
        from denoiser.integrations.aws import CloudWatchReader

        try:
            records = list(CloudWatchReader().read(log_group, log_stream))
        except Exception as e:
            raise ConnectorUnavailable(f"AWS CloudWatch not reachable: {e}") from e
        return Fetched(lines=[r.raw_text for r in records])

    @staticmethod
    def source_name(*, log_group: str, **_: Any) -> str:
        return f"aws_{log_group.replace('/', '_').strip('_')}.log"


class SimulatedAws:
    name = "aws"

    def discover(self) -> Discovery:
        return Discovery(
            simulated=True,
            message="AWS credentials not detected. Operating in sandbox mode.",
            items=[
                {"name": "/aws/lambda/payment-processor-prod", "arn": "arn:aws:logs:us-east-1:123:log-group:1", "stored_bytes": 4510200},
                {"name": "/aws/ecs/api-gateway-cluster", "arn": "arn:aws:logs:us-east-1:123:log-group:2", "stored_bytes": 128990100},
                {"name": "/aws/rds/db-primary-logs", "arn": "arn:aws:logs:us-east-1:123:log-group:3", "stored_bytes": 452912800},
                {"name": "/aws/vpc/flow-logs-public", "arn": "arn:aws:logs:us-east-1:123:log-group:4", "stored_bytes": 10982991000},
            ],
        )

    def fetch(self, **_: Any) -> Fetched:
        return Fetched(
            simulated=True,
            message="AWS credentials not detected. Generated sandbox log sequence.",
            lines=[
                "1715934500000\t[INFO]\tINIT\tContainer runtime: fargate-2.0",
                "1715934502000\t[INFO]\tSTART\tRequest ID: req-8219-cba0",
                "1715934505000\t[WARN]\tLATENCY\tDynamoDB batch_write took 450ms (threshold 100ms)",
                "1715934508000\t[ERROR]\tSNS\tFailed to publish event to topic: arn:aws:sns:us-east-1:123:notifications",
                "1715934510000\t[INFO]\tEND\tDuration: 520ms, Memory Used: 128MB",
            ],
        )


# ── Docker ───────────────────────────────────────────────────────────────────

class DockerConnector:
    name = "docker"

    def discover(self) -> Discovery:
        try:
            import docker

            containers = docker.from_env().containers.list(all=True)
        except Exception as e:
            raise ConnectorUnavailable(f"Docker daemon not reachable: {e}") from e
        return Discovery(items=[
            {
                "id": c.short_id,
                "name": c.name,
                "image": c.image.tags[0] if c.image.tags else "unknown",
                "status": c.status,
            }
            for c in containers
        ])

    def fetch(self, *, container_name: str, **_: Any) -> Fetched:
        try:
            import docker

            container = docker.from_env().containers.get(container_name)
            logs = container.logs(tail=1000).decode("utf-8")
        except Exception as e:
            raise ConnectorUnavailable(f"Docker daemon not reachable: {e}") from e
        return Fetched(lines=logs.splitlines())

    @staticmethod
    def source_name(*, container_name: str, **_: Any) -> str:
        return f"docker_{container_name}.log"


class SimulatedDocker:
    name = "docker"

    def discover(self) -> Discovery:
        return Discovery(
            simulated=True,
            message="Docker socket not detected. Operating in sandbox mode.",
            items=[
                {"id": "a2b9f3", "name": "nginx-ingress", "image": "nginx:alpine", "status": "running"},
                {"id": "c7d2e4", "name": "redis-cache", "image": "redis:7-alpine", "status": "running"},
                {"id": "f8e1a6", "name": "postgres-db", "image": "postgres:15-alpine", "status": "running"},
                {"id": "d4c9b8", "name": "node-api", "image": "node:20-slim", "status": "exited"},
            ],
        )

    def fetch(self, **_: Any) -> Fetched:
        return Fetched(
            simulated=True,
            message="Docker daemon not detected. Generated sandbox log sequence.",
            lines=[
                "node-api-1 | 2026-05-17 17:15:00 [info]: Express app listening on port 3000",
                "node-api-1 | 2026-05-17 17:15:02 [info]: Connected to PostgreSQL database at postgres-db:5432",
                "node-api-1 | 2026-05-17 17:15:04 [warn]: Redis cache connection missed for key 'user:123'",
                "node-api-1 | 2026-05-17 17:15:06 [error]: uncaughtException: Cannot read properties of undefined (reading 'email')",
                "node-api-1 | 2026-05-17 17:15:07 [info]: Process exited with code 1",
            ],
        )


#: provider -> (real adapter, sandbox adapter)
ADAPTERS: dict[str, tuple[type, type]] = {
    "k8s": (KubernetesConnector, SimulatedKubernetes),
    "aws": (AwsConnector, SimulatedAws),
    "docker": (DockerConnector, SimulatedDocker),
}


def discover(provider: str) -> Discovery:
    """What ``provider`` can see, falling back to sandbox data when permitted."""
    real, sandbox = ADAPTERS[provider]
    try:
        return real().discover()
    except ConnectorUnavailable:
        if not simulated_allowed():
            raise
        return sandbox().discover()


def fetch(provider: str, **params: Any) -> tuple[Fetched, str]:
    """Pull logs from ``provider``. Returns the lines and their source filename.

    The filename comes from the real adapter either way: a sandbox fetch should
    land where the real one would, so switching a deployment from sandbox to a
    live backend does not silently change which file the UI is pointing at.
    """
    real, sandbox = ADAPTERS[provider]
    filename = real.source_name(**params)
    try:
        return real().fetch(**params), filename
    except ConnectorUnavailable:
        if not simulated_allowed():
            raise
        return sandbox().fetch(**params), filename


def write_source(lines: list[str], filename: str, tenant_dir: Path) -> int:
    """Write fetched lines into the caller's own workspace. Returns the count.

    Into the *tenant's* directory, not the shared data root. Uploads have always
    landed under `data/tenants/{id}/`, but connector fetches wrote to the root —
    which `denoiser.api.sources` treats as the shared sample set every tenant may
    read. One customer pulling their production pod logs therefore published
    them, under a predictable filename, to every other customer on the
    deployment.
    """
    destination = tenant_dir / filename
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)
