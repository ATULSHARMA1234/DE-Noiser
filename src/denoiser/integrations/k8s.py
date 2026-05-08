"""
Kubernetes integration for fetching pod logs.
"""

from __future__ import annotations

from typing import Iterator

from kubernetes import client, config

from denoiser.exceptions import IngestionError
from denoiser.ingestion.models import LogRecord
from denoiser.logging import get_logger

logger = get_logger(__name__)


class KubernetesReader:
    """Reads logs directly from a Kubernetes Pod."""

    def __init__(self) -> None:
        """Initialize the Kubernetes client using the local kubeconfig."""
        try:
            config.load_kube_config()
            self.api = client.CoreV1Api()
            logger.debug("Successfully loaded local kubeconfig.")
        except Exception as e:
            raise IngestionError(f"Failed to load Kubernetes config. Make sure Minikube/Docker Desktop is running: {e}") from e

    def read(self, namespace: str, pod_name: str) -> Iterator[LogRecord]:
        """Fetch logs for a specific pod and yield LogRecords.

        Parameters
        ----------
        namespace : str
            The Kubernetes namespace.
        pod_name : str
            The name of the pod.

        Yields
        ------
        LogRecord
            A record for each log line.
        """
        logger.info(f"Fetching logs from Kubernetes pod {namespace}/{pod_name}...")
        
        try:
            # We fetch the logs as a single string. For massive logs, stream=True is better, 
            # but for this MVP, standard read is highly reliable.
            pod_logs = self.api.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
            )
        except Exception as e:
            raise IngestionError(f"Failed to read logs for pod {pod_name} in namespace {namespace}: {e}") from e

        if not pod_logs:
            logger.warning(f"No logs found for pod {pod_name}.")
            return

        source_name = f"k8s://{namespace}/{pod_name}"
        lines = pod_logs.splitlines()
        
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue

            yield LogRecord(
                raw_text=line,
                source=source_name,
                line_number=i,
            )
