"""GitHub integration: Actions logs, incident issues, and deployment metadata.

Log fetching and deployment sync used to raise ``NotImplementedError`` — honest,
but it left CI failures outside the platform entirely, which is where a large
share of the incidents a log-intelligence tool is supposed to explain actually
start. Both are now real REST calls.

Nothing here fabricates data: an unconfigured integration, an expired token, a
rate-limited API or a repo with no runs each surface as an error or an empty
result that says which it was, never as sample logs.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from denoiser.integrations.manager import IntegrationProvider
from denoiser.logging import get_logger

logger = get_logger(__name__)

API_ROOT = "https://api.github.com"
# Actions log lines are prefixed with an RFC3339 timestamp at 100ns precision.
LOG_LINE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+Z)\s?(?P<message>.*)$")
LEVEL_HINTS = (
    ("error", ("##[error]", "error:", " error ", "fatal", "exception", "failed")),
    ("warning", ("##[warning]", "warning:", " warn ")),
    ("debug", ("##[debug]",)),
)


class GitHubError(Exception):
    """A GitHub API call that failed. Never swallowed into fake data."""


def parse_time_range(time_range: str | None) -> timedelta:
    """Parse ``30m`` / ``6h`` / ``7d`` into a lookback window (default 24h)."""
    if not time_range:
        return timedelta(hours=24)
    match = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", time_range.lower())
    if not match:
        raise GitHubError(f"Unrecognised time range {time_range!r}; use forms like 30m, 6h, 7d")
    amount, unit = int(match.group(1)), match.group(2)
    return {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
        "w": timedelta(weeks=amount),
    }[unit]


def _classify(message: str) -> str:
    lowered = message.lower()
    for level, markers in LEVEL_HINTS:
        if any(marker in lowered for marker in markers):
            return level
    return "info"


class GitHubIntegration(IntegrationProvider):
    """GitHub integration over the REST API. ``repo`` must be "owner/name"."""

    def __init__(self, api_token: str, repo: str | None = None, timeout: float = 30.0):
        self.api_token = api_token
        self.repo = repo
        self.timeout = timeout
        # A single workflow run's log archive can be large; both caps are
        # env-tunable so a busy monorepo can be pulled in bounded slices.
        self.max_runs = int(os.getenv("GITHUB_MAX_WORKFLOW_RUNS", "10"))
        self.max_lines_per_run = int(os.getenv("GITHUB_MAX_LINES_PER_RUN", "5000"))

    def get_provider_name(self) -> str:
        return "GitHub"

    # ── HTTP plumbing ───────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _require_config(self) -> None:
        if not self.api_token or not self.repo:
            raise GitHubError("GitHub integration is not configured (needs api_token and repo)")
        if "/" not in self.repo:
            raise GitHubError(f"repo must be 'owner/name', got {self.repo!r}")

    def _get(self, path: str, *, params: dict | None = None, follow_redirects: bool = True) -> httpx.Response:
        url = f"{API_ROOT}{path}"
        try:
            resp = httpx.get(
                url, headers=self._headers(), params=params,
                timeout=self.timeout, follow_redirects=follow_redirects,
            )
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub request to {path} failed: {e}")

        if resp.status_code == 200:
            return resp
        if resp.status_code in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
            reset = resp.headers.get("x-ratelimit-reset", "unknown")
            raise GitHubError(f"GitHub API rate limit exhausted (resets at {reset})")
        if resp.status_code == 401:
            raise GitHubError("GitHub token is invalid or expired")
        if resp.status_code == 404:
            raise GitHubError(f"GitHub returned 404 for {path} — check the repo name and token scopes")
        raise GitHubError(f"GitHub returned {resp.status_code} for {path}: {resp.text[:200]}")

    # ── Logs ────────────────────────────────────────────────────────────

    def list_workflow_runs(self, since: datetime, query: str | None = None) -> list[dict[str, Any]]:
        """Workflow runs started since ``since``, newest first.

        ``query`` filters on workflow name, branch or conclusion — so
        ``"failure"`` narrows to failed runs, which is the common case when
        something is being investigated.
        """
        self._require_config()
        params = {
            "created": f">={since.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "per_page": min(100, max(self.max_runs, 1)),
        }
        payload = self._get(f"/repos/{self.repo}/actions/runs", params=params).json()
        runs = payload.get("workflow_runs", []) or []

        if query:
            needle = query.strip().lower()
            runs = [
                run for run in runs
                if needle in (run.get("name") or "").lower()
                or needle in (run.get("head_branch") or "").lower()
                or needle == (run.get("conclusion") or "").lower()
                or needle == (run.get("status") or "").lower()
            ]
        return runs[: self.max_runs]

    def _fetch_run_log_lines(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        """Download and flatten one run's log archive into log entries."""
        run_id = run.get("id")
        try:
            resp = self._get(f"/repos/{self.repo}/actions/runs/{run_id}/logs")
        except GitHubError as e:
            # Logs expire (90 days by default) and in-progress runs have none;
            # one unavailable archive must not fail the whole fetch.
            logger.warning("Skipping logs for run %s: %s", run_id, e)
            return []

        try:
            archive = zipfile.ZipFile(io.BytesIO(resp.content))
        except zipfile.BadZipFile:
            logger.warning("Run %s log archive is not a readable zip", run_id)
            return []

        entries: list[dict[str, Any]] = []
        workflow = run.get("name") or "workflow"
        for name in sorted(archive.namelist()):
            if not name.endswith(".txt"):
                continue
            job = name.rsplit("/", 1)[-1].removesuffix(".txt")
            with archive.open(name) as handle:
                for raw in io.TextIOWrapper(handle, encoding="utf-8", errors="replace"):
                    line = raw.rstrip("\n")
                    if not line.strip():
                        continue
                    match = LOG_LINE.match(line)
                    timestamp = match.group("ts") if match else None
                    message = match.group("message") if match else line
                    if not message.strip():
                        continue
                    entries.append({
                        "timestamp": timestamp,
                        "message": message,
                        "level": _classify(message),
                        "service": f"github-actions/{workflow}",
                        "source": f"github:{self.repo}",
                        "run_id": run_id,
                        "workflow": workflow,
                        "job": job,
                        "branch": run.get("head_branch"),
                        "conclusion": run.get("conclusion"),
                        "url": run.get("html_url"),
                    })
                    if len(entries) >= self.max_lines_per_run:
                        logger.info(
                            "Run %s hit the %d-line cap; log slice is truncated",
                            run_id, self.max_lines_per_run,
                        )
                        return entries
        return entries

    def fetch_logs(self, query: str, time_range: str) -> list[dict[str, Any]]:
        """Fetch GitHub Actions logs for recent workflow runs.

        ``query`` filters runs (workflow name, branch, or a conclusion such as
        ``failure``); ``time_range`` is a lookback like ``6h`` or ``7d``. Raises
        :class:`GitHubError` rather than returning anything invented when the
        integration is unconfigured or the API refuses the call.
        """
        self._require_config()
        since = datetime.now(UTC) - parse_time_range(time_range)
        runs = self.list_workflow_runs(since, query)
        if not runs:
            logger.info("No GitHub Actions runs matched query=%r within %s", query, time_range)
            return []

        logs: list[dict[str, Any]] = []
        for run in runs:
            logs.extend(self._fetch_run_log_lines(run))
        logger.info("Fetched %d GitHub Actions log lines across %d runs", len(logs), len(runs))
        return logs

    # ── Alerts ──────────────────────────────────────────────────────────

    def send_alert(self, incident: Any) -> bool:
        """Create a real GitHub Issue for an incident. Returns delivery success.

        Returns False (never a fake True) when the integration isn't fully
        configured or the API call fails, so callers don't record a delivery
        that never happened.
        """
        if not self.api_token or not self.repo:
            logger.warning("GitHub integration not configured (need api_token + repo); alert not sent")
            return False
        try:
            resp = httpx.post(
                f"{API_ROOT}/repos/{self.repo}/issues",
                headers=self._headers(),
                json={
                    "title": f"[SemanticOS] {getattr(incident, 'title', 'Incident')}",
                    "body": getattr(incident, "summary", "") or f"Incident {getattr(incident, 'id', '?')}",
                },
                timeout=10,
            )
            if resp.status_code in (200, 201):
                return True
            logger.error(f"GitHub issue creation failed ({resp.status_code}): {resp.text}")
            return False
        except Exception as e:
            logger.error(f"GitHub issue creation error: {e}")
            return False

    # ── Metadata ────────────────────────────────────────────────────────

    def sync_metadata(self) -> dict[str, Any]:
        """Deployments, latest release and repo facts, for deploy↔incident correlation.

        Raises :class:`GitHubError` if the repo itself cannot be read. The
        deployments and releases endpoints are optional — a repo may legitimately
        have neither — so those degrade to empty/None with the reason logged,
        rather than failing the whole sync.
        """
        self._require_config()
        repo_info = self._get(f"/repos/{self.repo}").json()

        deployments: list[dict[str, Any]] = []
        try:
            for deployment in self._get(f"/repos/{self.repo}/deployments", params={"per_page": 20}).json():
                deployments.append({
                    "id": deployment.get("id"),
                    "sha": deployment.get("sha"),
                    "ref": deployment.get("ref"),
                    "environment": deployment.get("environment"),
                    "created_at": deployment.get("created_at"),
                    "creator": (deployment.get("creator") or {}).get("login"),
                    "description": deployment.get("description"),
                })
        except GitHubError as e:
            logger.warning("GitHub deployments unavailable for %s: %s", self.repo, e)

        latest_release: dict[str, Any] | None = None
        try:
            release = self._get(f"/repos/{self.repo}/releases/latest").json()
            latest_release = {
                "tag": release.get("tag_name"),
                "name": release.get("name"),
                "published_at": release.get("published_at"),
                "url": release.get("html_url"),
            }
        except GitHubError as e:
            logger.info("No latest release for %s: %s", self.repo, e)

        return {
            "provider": "GitHub",
            "repo": self.repo,
            "default_branch": repo_info.get("default_branch"),
            "private": repo_info.get("private"),
            "pushed_at": repo_info.get("pushed_at"),
            "open_issues": repo_info.get("open_issues_count"),
            "deployments": deployments,
            "latest_release": latest_release,
            "synced_at": datetime.now(UTC).isoformat(),
        }


def handle_github_webhook(payload: dict[str, Any]):
    """
    Handle incoming GitHub webhooks (e.g., push, release).
    """
    event = payload.get("action", "unknown")
    if event == "published":
        logger.info(f"Received new release from GitHub: {payload.get('release', {}).get('tag_name')}")
