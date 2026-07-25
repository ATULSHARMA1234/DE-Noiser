from typing import Any

import httpx

from denoiser.integrations.manager import IntegrationProvider
from denoiser.logging import get_logger

logger = get_logger(__name__)

class GitHubIntegration(IntegrationProvider):
    """GitHub integration: create issues for incidents via the REST API.

    ``repo`` must be "owner/name". Log fetching and deployment sync are not
    implemented and raise rather than returning fabricated data.
    """

    def __init__(self, api_token: str, repo: str | None = None):
        self.api_token = api_token
        self.repo = repo

    def get_provider_name(self) -> str:
        return "GitHub"

    def fetch_logs(self, query: str, time_range: str) -> list[dict[str, Any]]:
        # GitHub Actions log retrieval is not implemented. Don't fabricate data.
        raise NotImplementedError("GitHub Actions log fetching is not implemented")

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
                f"https://api.github.com/repos/{self.repo}/issues",
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Accept": "application/vnd.github+json",
                },
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

    def sync_metadata(self) -> dict[str, Any]:
        """Deployment sync is not implemented."""
        raise NotImplementedError("GitHub deployment sync is not implemented")

def handle_github_webhook(payload: dict[str, Any]):
    """
    Handle incoming GitHub webhooks (e.g., push, release).
    """
    event = payload.get("action", "unknown")
    if event == "published":
        logger.info(f"Received new release from GitHub: {payload.get('release', {}).get('tag_name')}")
