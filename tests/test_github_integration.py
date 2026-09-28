"""Tests for the GitHub integration.

fetch_logs and sync_metadata used to raise NotImplementedError. These cover the
real REST paths against mocked HTTP: Actions log archives are unzipped and
flattened into log entries, deployment metadata comes back from the API, and
every failure mode (bad token, rate limit, missing repo) surfaces as an error
rather than as invented data.
"""

import io
import zipfile

import pytest
import respx
from httpx import Response

from denoiser.integrations.github import (
    GitHubError,
    GitHubIntegration,
    parse_time_range,
)

REPO = "acme/payments-api"
RUNS_URL = f"https://api.github.com/repos/{REPO}/actions/runs"
REPO_URL = f"https://api.github.com/repos/{REPO}"


def _log_archive(lines_by_job: dict[str, list[str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for job, lines in lines_by_job.items():
            archive.writestr(f"build/{job}.txt", "\n".join(lines))
    return buffer.getvalue()


RUN = {
    "id": 981,
    "name": "CI",
    "head_branch": "main",
    "status": "completed",
    "conclusion": "failure",
    "html_url": f"https://github.com/{REPO}/actions/runs/981",
}

ARCHIVE = _log_archive({
    "test": [
        "2026-07-25T10:00:00.1234567Z Running pytest",
        "2026-07-25T10:00:04.7654321Z ##[error]tests/test_billing.py::test_charge FAILED",
        "",
        "2026-07-25T10:00:05.0000000Z ##[warning]retrying flaky fixture",
    ],
    "lint": ["2026-07-25T10:00:01.0000000Z ruff: all checks passed"],
})


@pytest.fixture
def integration() -> GitHubIntegration:
    return GitHubIntegration(api_token="ghp_testtoken", repo=REPO)


class TestTimeRangeParsing:
    def test_supported_units(self):
        assert parse_time_range("30m").total_seconds() == 1800
        assert parse_time_range("6h").total_seconds() == 21600
        assert parse_time_range("7d").days == 7
        assert parse_time_range(None).total_seconds() == 86400

    def test_garbage_range_is_rejected(self):
        with pytest.raises(GitHubError, match="Unrecognised time range"):
            parse_time_range("last tuesday")


class TestFetchLogs:
    @respx.mock
    def test_actions_logs_are_flattened_into_entries(self, integration):
        respx.get(RUNS_URL).mock(return_value=Response(200, json={"workflow_runs": [RUN]}))
        respx.get(f"{RUNS_URL}/981/logs").mock(return_value=Response(200, content=ARCHIVE))

        logs = integration.fetch_logs(query="", time_range="24h")

        assert len(logs) == 4  # blank line dropped
        error = next(entry for entry in logs if "FAILED" in entry["message"])
        assert error["level"] == "error"
        assert error["timestamp"] == "2026-07-25T10:00:04.7654321Z"
        assert error["run_id"] == 981
        assert error["job"] == "test"
        assert error["service"] == "github-actions/CI"
        assert error["source"] == f"github:{REPO}"
        assert error["branch"] == "main"
        # The timestamp prefix is stripped from the message itself.
        assert not error["message"].startswith("2026-")
        assert any(entry["level"] == "warning" for entry in logs)
        assert any(entry["level"] == "info" for entry in logs)

    @respx.mock
    def test_query_filters_runs_by_conclusion(self, integration):
        passing = {**RUN, "id": 982, "conclusion": "success", "name": "Nightly"}
        respx.get(RUNS_URL).mock(
            return_value=Response(200, json={"workflow_runs": [RUN, passing]})
        )
        logs_route = respx.get(f"{RUNS_URL}/981/logs").mock(
            return_value=Response(200, content=ARCHIVE)
        )

        logs = integration.fetch_logs(query="failure", time_range="24h")

        assert logs_route.called
        assert {entry["run_id"] for entry in logs} == {981}

    @respx.mock
    def test_no_matching_runs_returns_empty_not_sample_data(self, integration):
        respx.get(RUNS_URL).mock(return_value=Response(200, json={"workflow_runs": []}))
        assert integration.fetch_logs(query="", time_range="1h") == []

    @respx.mock
    def test_expired_log_archive_skips_that_run_only(self, integration):
        second = {**RUN, "id": 982}
        respx.get(RUNS_URL).mock(return_value=Response(200, json={"workflow_runs": [RUN, second]}))
        respx.get(f"{RUNS_URL}/981/logs").mock(return_value=Response(410, json={"message": "gone"}))
        respx.get(f"{RUNS_URL}/982/logs").mock(return_value=Response(200, content=ARCHIVE))

        logs = integration.fetch_logs(query="", time_range="24h")
        assert {entry["run_id"] for entry in logs} == {982}

    @respx.mock
    def test_line_cap_truncates_instead_of_exhausting_memory(self, integration):
        integration.max_lines_per_run = 2
        respx.get(RUNS_URL).mock(return_value=Response(200, json={"workflow_runs": [RUN]}))
        respx.get(f"{RUNS_URL}/981/logs").mock(return_value=Response(200, content=ARCHIVE))
        assert len(integration.fetch_logs(query="", time_range="24h")) == 2

    @respx.mock
    def test_invalid_token_raises(self, integration):
        respx.get(RUNS_URL).mock(return_value=Response(401, json={"message": "Bad credentials"}))
        with pytest.raises(GitHubError, match="invalid or expired"):
            integration.fetch_logs(query="", time_range="24h")

    @respx.mock
    def test_rate_limit_is_reported_as_such(self, integration):
        respx.get(RUNS_URL).mock(
            return_value=Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000000"},
            )
        )
        with pytest.raises(GitHubError, match="rate limit exhausted"):
            integration.fetch_logs(query="", time_range="24h")

    @respx.mock
    def test_unknown_repo_raises(self, integration):
        respx.get(RUNS_URL).mock(return_value=Response(404, json={"message": "Not Found"}))
        with pytest.raises(GitHubError, match="404"):
            integration.fetch_logs(query="", time_range="24h")

    def test_unconfigured_integration_raises(self):
        with pytest.raises(GitHubError, match="not configured"):
            GitHubIntegration(api_token="", repo=REPO).fetch_logs("", "24h")
        with pytest.raises(GitHubError, match="owner/name"):
            GitHubIntegration(api_token="t", repo="just-a-name").fetch_logs("", "24h")


class TestSyncMetadata:
    @respx.mock
    def test_deployments_and_release_are_returned(self, integration):
        respx.get(REPO_URL).mock(return_value=Response(200, json={
            "default_branch": "main", "private": True,
            "pushed_at": "2026-07-25T09:00:00Z", "open_issues_count": 7,
        }))
        respx.get(f"{REPO_URL}/deployments").mock(return_value=Response(200, json=[{
            "id": 5, "sha": "abc123", "ref": "main", "environment": "production",
            "created_at": "2026-07-25T08:00:00Z", "creator": {"login": "release-bot"},
            "description": "Deploy 2.4.1",
        }]))
        respx.get(f"{REPO_URL}/releases/latest").mock(return_value=Response(200, json={
            "tag_name": "v2.4.1", "name": "2.4.1",
            "published_at": "2026-07-25T07:30:00Z",
            "html_url": f"https://github.com/{REPO}/releases/tag/v2.4.1",
        }))

        metadata = integration.sync_metadata()

        assert metadata["repo"] == REPO
        assert metadata["default_branch"] == "main"
        assert metadata["deployments"][0]["environment"] == "production"
        assert metadata["deployments"][0]["creator"] == "release-bot"
        assert metadata["latest_release"]["tag"] == "v2.4.1"
        assert metadata["synced_at"]

    @respx.mock
    def test_repo_without_releases_still_syncs(self, integration):
        respx.get(REPO_URL).mock(return_value=Response(200, json={"default_branch": "trunk"}))
        respx.get(f"{REPO_URL}/deployments").mock(return_value=Response(200, json=[]))
        respx.get(f"{REPO_URL}/releases/latest").mock(return_value=Response(404, json={}))

        metadata = integration.sync_metadata()
        assert metadata["latest_release"] is None
        assert metadata["deployments"] == []

    @respx.mock
    def test_unreadable_repo_raises_rather_than_returning_a_shell(self, integration):
        respx.get(REPO_URL).mock(return_value=Response(404, json={"message": "Not Found"}))
        with pytest.raises(GitHubError):
            integration.sync_metadata()


class TestSendAlert:
    @respx.mock
    def test_issue_creation_success(self, integration):
        respx.post(f"{REPO_URL}/issues").mock(return_value=Response(201, json={"number": 12}))
        incident = type("Incident", (), {"id": 3, "title": "DB timeouts", "summary": "pool exhausted"})()
        assert integration.send_alert(incident) is True

    @respx.mock
    def test_failed_issue_creation_reports_false(self, integration):
        respx.post(f"{REPO_URL}/issues").mock(return_value=Response(422, json={"message": "nope"}))
        incident = type("Incident", (), {"id": 3, "title": "DB timeouts", "summary": ""})()
        assert integration.send_alert(incident) is False
