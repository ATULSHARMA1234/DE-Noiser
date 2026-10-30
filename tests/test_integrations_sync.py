"""Integrations beyond CRUD: the connectivity check, deployment sync, delete.

tests/test_integrations_api.py covers create/update and credential masking.
`/test` and `/sync` — the endpoints that make an integration do anything — and
delete had no tests. GitHub is stubbed at `sync_metadata`, the one method that
talks to it.
"""

import pytest

from denoiser.integrations.github import GitHubIntegration
from denoiser.storage.db import DeploymentMarker, SessionLocal

METADATA = {
    "repo": "acme/checkout-api",
    "default_branch": "main",
    "synced_at": "2026-10-30T10:00:00Z",
    "latest_release": {"tag": "v2.4.0"},
    "deployments": [
        {"sha": "a1b2c3d4e5f6a7b8c9d0", "ref": "v2.4.0", "environment": "production",
         "created_at": "2026-10-29T08:00:00Z", "description": "release"},
        {"sha": "0f9e8d7c6b5a49382716", "ref": "main", "environment": "staging",
         "created_at": "2026-10-28T08:00:00Z"},
    ],
}


@pytest.fixture
def github(two_orgs, monkeypatch):
    a, b, as_user = two_orgs
    calls = []

    def fake_sync(self):
        calls.append(self.repo)
        return METADATA

    monkeypatch.setattr(GitHubIntegration, "sync_metadata", fake_sync)
    client = as_user(a)
    created = client.post("/integrations", json={
        "provider": "github", "name": "checkout", "config": {"api_key": "ghp_x", "repo": "acme/checkout-api"},
    }).json()
    return a, b, as_user, client, created["id"], calls


def _markers(tenant_id):
    db = SessionLocal()
    try:
        return sorted(
            (m.service, m.version, m.environment)
            for m in db.query(DeploymentMarker).filter(DeploymentMarker.tenant_id == tenant_id)
        )
    finally:
        db.close()


class TestConnectivityCheck:
    def test_working_credentials(self, github):
        _, _, _, client, iid, calls = github
        body = client.post(f"/integrations/{iid}/test").json()
        assert body["status"] == "ok"
        assert "acme/checkout-api" in body["detail"] and "main" in body["detail"]
        assert calls == ["acme/checkout-api"]

    def test_failing_credentials_say_why(self, github, monkeypatch):
        _, _, _, client, iid, _ = github

        def bad(self):
            raise RuntimeError("401 Bad credentials")

        monkeypatch.setattr(GitHubIntegration, "sync_metadata", bad)
        assert client.post(f"/integrations/{iid}/test").json() == {"status": "failed", "detail": "401 Bad credentials"}

    def test_a_provider_with_no_check_says_so(self, two_orgs):
        a, _, as_user = two_orgs
        client = as_user(a)
        iid = client.post("/integrations", json={"provider": "datadog", "name": "dd", "config": {}}).json()["id"]
        assert client.post(f"/integrations/{iid}/test").json()["status"] == "unsupported"

    def test_viewer_cannot_run_it(self, github):
        a, _, as_user, _, iid, _ = github
        assert as_user(a, role="VIEWER").post(f"/integrations/{iid}/test").status_code == 403


class TestDeploymentSync:
    def test_deployments_become_markers(self, github):
        a, _, _, client, iid, _ = github
        body = client.post(f"/integrations/{iid}/sync").json()
        assert body["deployments_imported"] == 2 and body["latest_release"] == "v2.4.0"
        assert _markers(a) == [
            ("checkout-api", "0f9e8d7c6b5a", "staging"),
            ("checkout-api", "a1b2c3d4e5f6", "production"),
        ]

    def test_repeat_sync_does_not_duplicate(self, github):
        a, _, _, client, iid, _ = github
        client.post(f"/integrations/{iid}/sync")
        assert client.post(f"/integrations/{iid}/sync").json()["deployments_imported"] == 0
        assert len(_markers(a)) == 2

    def test_service_can_be_mapped_per_environment(self, github):
        a, _, _, client, iid, _ = github
        client.put(f"/integrations/{iid}", json={"config": {
            "api_key": "ghp_x", "repo": "acme/checkout-api",
            "service": "checkout", "service_by_environment": {"staging": "checkout-canary"},
        }})
        client.post(f"/integrations/{iid}/sync")
        assert {s for s, _, _ in _markers(a)} == {"checkout", "checkout-canary"}

    def test_sync_records_what_it_saw(self, github):
        _, _, _, client, iid, _ = github
        client.post(f"/integrations/{iid}/sync")
        listed = next(i for i in client.get("/integrations").json() if i["id"] == iid)
        assert listed["config"]["default_branch"] == "main"
        assert listed["config"]["latest_release"] == "v2.4.0"
        assert listed["config"]["last_synced_at"] == "2026-10-30T10:00:00Z"

    def test_markers_belong_to_the_syncing_organisation_only(self, github):
        _, b, _, client, iid, _ = github
        client.post(f"/integrations/{iid}/sync")
        assert _markers(b) == []

    def test_provider_failure_is_502(self, github, monkeypatch):
        _, _, _, client, iid, _ = github

        def down(self):
            raise RuntimeError("GitHub unreachable")

        monkeypatch.setattr(GitHubIntegration, "sync_metadata", down)
        res = client.post(f"/integrations/{iid}/sync")
        assert res.status_code == 502 and "GitHub unreachable" in res.json()["detail"]

    def test_provider_without_sync_is_400(self, two_orgs):
        a, _, as_user = two_orgs
        client = as_user(a)
        iid = client.post("/integrations", json={"provider": "datadog", "name": "dd", "config": {}}).json()["id"]
        assert client.post(f"/integrations/{iid}/sync").status_code == 400


class TestDelete:
    def test_admin_deletes(self, github):
        _, _, _, client, iid, _ = github
        assert client.delete(f"/integrations/{iid}").status_code == 200
        assert iid not in {i["id"] for i in client.get("/integrations").json()}
        assert client.post(f"/integrations/{iid}/test").status_code == 404

    def test_only_admin_and_only_the_owner(self, github):
        a, b, as_user, _, iid, _ = github
        assert as_user(a, role="ANALYST").delete(f"/integrations/{iid}").status_code == 403
        assert as_user(b).delete(f"/integrations/{iid}").status_code == 404
        assert as_user(b).post(f"/integrations/{iid}/sync").status_code == 404
