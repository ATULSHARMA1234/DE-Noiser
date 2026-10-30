"""Explore's `data/*.log` fallback is a development aid, not a production path.

When ClickHouse failed, every Explore endpoint silently scanned a shared
directory instead, `/query/log-files` listed that directory to every tenant,
and `file_name` was joined onto the path unsanitised.
"""

import pytest
from fastapi.testclient import TestClient

from denoiser import runtime
from denoiser.api import query as query_api
from denoiser.storage.db import User


class _BrokenClient:
    @staticmethod
    def query(*_a, **_k):
        raise RuntimeError("clickhouse down")


class _BrokenClickHouse:
    client = _BrokenClient()

    @staticmethod
    def scope(*_a, **_k):
        return "1", {}

    @staticmethod
    def get_facets(**_k):
        raise RuntimeError("clickhouse down")

    @staticmethod
    def get_histogram(**_k):
        raise RuntimeError("clickhouse down")


@pytest.fixture
def client(monkeypatch):
    from denoiser.api.auth import get_current_user
    from denoiser.api.main import app

    monkeypatch.setattr(runtime, "clickhouse_store", lambda: _BrokenClickHouse())
    app.dependency_overrides[get_current_user] = lambda: User(id=1, email="t@x.io", role="VIEWER", tenant_id=1)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestProduction:
    @pytest.fixture(autouse=True)
    def _production(self, monkeypatch):
        monkeypatch.setenv("ALLOW_FILE_QUERY_FALLBACK", "false")

    def test_query_answers_503_instead_of_scanning_files(self, client):
        assert client.post("/query", json={"query": "error"}).status_code == 503

    def test_facets_and_histogram_also_refuse(self, client):
        assert client.get("/query/facets").status_code == 503
        assert client.post("/query/histogram", json={"query": "error"}).status_code == 503

    def test_shared_files_are_not_listed(self, client, tmp_path, monkeypatch):
        (tmp_path / "other-tenant.log").write_text("x\n")
        monkeypatch.setattr(query_api, "DATA_DIR", tmp_path)
        assert client.get("/query/log-files").json() == {"files": []}


class TestDevelopment:
    @pytest.fixture(autouse=True)
    def _dev(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALLOW_FILE_QUERY_FALLBACK", "true")
        data = tmp_path / "data"
        data.mkdir()
        (data / "app.log").write_text('{"timestamp": 1, "message": "boom", "level": "ERROR", "_tenant_id": 1}\n')
        (tmp_path / "secret.log").write_text('{"timestamp": 1, "message": "outside", "_tenant_id": 1}\n')
        monkeypatch.setattr(query_api, "DATA_DIR", data)

    def test_fallback_still_serves_developers(self, client):
        body = client.post("/query", json={"query": "boom"}).json()
        assert body["engine"] == "in-memory"
        assert [log["message"] for log in body["logs"]] == ["boom"]

    def test_file_name_cannot_leave_the_data_directory(self, client):
        body = client.post("/query", json={"query": "", "file_name": "../secret.log"}).json()
        assert all(log["message"] != "outside" for log in body["logs"])
