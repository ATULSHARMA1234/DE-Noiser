"""Both sides of every connector, including the one that was untestable.

The real fetch used to live inside an HTTP route body, wrapped in a bare
`except Exception` that fell through to fabricated sandbox data. A test could
reach the simulated branch and nothing else, so the code that actually talks to
Kubernetes, CloudWatch and Docker had no coverage — and "the backend is down"
and "the backend returned nothing" were indistinguishable from outside.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from denoiser.integrations import connectors
from denoiser.integrations.connectors import (
    AwsConnector,
    ConnectorUnavailable,
    DockerConnector,
    KubernetesConnector,
)


class _Record:
    def __init__(self, raw_text):
        self.raw_text = raw_text


class TestTheRealAdaptersReportFailureRatherThanSwallowIt:
    """Each raises `ConnectorUnavailable`; the *caller* decides what that means."""

    def test_kubernetes_unreachable(self):
        with patch("denoiser.integrations.k8s.KubernetesReader") as reader:
            reader.return_value.list_pods.side_effect = OSError("no kubeconfig")
            with pytest.raises(ConnectorUnavailable, match="Kubernetes API not reachable"):
                KubernetesConnector().discover()

    def test_aws_unreachable(self):
        with patch("denoiser.integrations.aws.build_logs_client") as build:
            build.side_effect = RuntimeError("no credentials")
            with pytest.raises(ConnectorUnavailable, match="AWS CloudWatch not reachable"):
                AwsConnector().discover()

    def test_docker_unreachable(self):
        with patch.dict("sys.modules", {"docker": MagicMock()}) as mods:
            mods["docker"].from_env.side_effect = RuntimeError("no socket")
            with pytest.raises(ConnectorUnavailable, match="Docker daemon not reachable"):
                DockerConnector().discover()


class TestTheRealAdaptersOnTheHappyPath:
    """None of this was reachable from a test before."""

    def test_kubernetes_lists_pods_and_caps_the_page(self):
        with patch("denoiser.integrations.k8s.KubernetesReader") as reader:
            reader.return_value.list_pods.return_value = [
                {"name": f"pod-{i}"} for i in range(120)
            ]
            found = KubernetesConnector().discover()

        assert found.simulated is False
        assert len(found.items) == 50

    def test_kubernetes_fetch_returns_the_raw_lines(self):
        with patch("denoiser.integrations.k8s.KubernetesReader") as reader:
            reader.return_value.read.return_value = iter(
                [_Record("line one"), _Record("line two")]
            )
            fetched = KubernetesConnector().fetch(namespace="prod", pod_name="api-1")

        assert fetched.lines == ["line one", "line two"]
        assert fetched.simulated is False

    def test_aws_maps_the_cloudwatch_group_shape(self):
        with patch("denoiser.integrations.aws.build_logs_client") as build:
            build.return_value.describe_log_groups.return_value = {
                "logGroups": [
                    {"logGroupName": "/aws/lambda/x", "arn": "arn:1", "storedBytes": 10},
                    {"logGroupName": "/aws/ecs/y", "arn": "arn:2"},  # no storedBytes
                ]
            }
            found = AwsConnector().discover()

        assert found.items[0] == {
            "name": "/aws/lambda/x", "arn": "arn:1", "stored_bytes": 10
        }
        assert found.items[1]["stored_bytes"] == 0, "a missing size is 0, not absent"

    def test_docker_lists_containers_and_survives_an_untagged_image(self):
        container = MagicMock(short_id="abc123", status="running")
        # `name` is a MagicMock constructor keyword, so it has to be set after.
        container.name = "api"
        container.image.tags = []
        docker = MagicMock()
        docker.from_env.return_value.containers.list.return_value = [container]

        with patch.dict("sys.modules", {"docker": docker}):
            found = DockerConnector().discover()

        assert found.items[0]["image"] == "unknown"
        assert found.items[0]["id"] == "abc123"
        assert found.items[0]["name"] == "api"


class TestChoosingAnAdapter:
    def test_a_reachable_backend_is_never_substituted(self):
        with patch("denoiser.integrations.k8s.KubernetesReader") as reader:
            reader.return_value.list_pods.return_value = [{"name": "real-pod"}]
            found = connectors.discover("k8s")

        assert found.simulated is False
        assert found.items == [{"name": "real-pod"}]

    def test_an_unreachable_backend_falls_back_when_permitted(self, monkeypatch):
        monkeypatch.setattr(connectors, "simulated_allowed", lambda: True)
        with patch("denoiser.integrations.k8s.KubernetesReader") as reader:
            reader.return_value.list_pods.side_effect = OSError("down")
            found = connectors.discover("k8s")

        assert found.simulated is True
        assert found.items, "the sandbox answers with something to look at"

    def test_an_unreachable_backend_raises_when_not_permitted(self, monkeypatch):
        """In production, fake infrastructure is worse than an error."""
        monkeypatch.setattr(connectors, "simulated_allowed", lambda: False)
        with patch("denoiser.integrations.k8s.KubernetesReader") as reader:
            reader.return_value.list_pods.side_effect = OSError("down")
            with pytest.raises(ConnectorUnavailable):
                connectors.discover("k8s")

    def test_a_sandbox_fetch_lands_where_the_real_one_would(self, monkeypatch):
        """Otherwise pointing a deployment at a live backend silently changes
        which file the UI has been told to analyse."""
        monkeypatch.setattr(connectors, "simulated_allowed", lambda: True)
        with patch("denoiser.integrations.k8s.KubernetesReader") as reader:
            reader.return_value.read.side_effect = OSError("down")
            fetched, filename = connectors.fetch(
                "k8s", namespace="prod", pod_name="api-1"
            )

        assert fetched.simulated is True
        assert filename == "k8s_prod_api-1.log"

    def test_the_aws_source_name_is_filesystem_safe(self):
        filename = AwsConnector.source_name(log_group="/aws/lambda/payments")
        assert filename == "aws_aws_lambda_payments.log"
        assert "/" not in filename


class TestFetchedLogsStayInTheCallersWorkspace:
    def test_a_fetch_writes_into_the_tenants_own_directory(self, tmp_path):
        """Connector fetches used to write to the shared data root, which
        `denoiser.api.sources` treats as the sample set every tenant may read —
        so one customer pulling their production pod logs published them, under
        a predictable filename, to every other customer on the deployment."""
        workspace = tmp_path / "tenants" / "7"
        workspace.mkdir(parents=True)

        count = connectors.write_source(["a", "b"], "k8s_prod_api.log", workspace)

        assert count == 2
        assert (workspace / "k8s_prod_api.log").read_text() == "a\nb\n"
        assert not (tmp_path / "k8s_prod_api.log").exists()

    def test_an_empty_fetch_writes_an_empty_file_not_a_stray_newline(self, tmp_path):
        connectors.write_source([], "empty.log", tmp_path)
        assert (tmp_path / "empty.log").read_text() == ""
