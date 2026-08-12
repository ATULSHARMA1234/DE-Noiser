"""Elasticsearch Bulk API and Splunk HEC compatibility: parsing, endpoints,
auth, and pipeline compatibility."""

import pytest
from fastapi.testclient import TestClient

from denoiser.api.compat import parse_bulk, parse_hec
from denoiser.storage.clickhouse_store import resolve_level, resolve_source, resolve_timestamp

API_KEY = "semanticos-ingest-key-123"


@pytest.fixture(scope="module", autouse=True)
def _db():
    from denoiser.storage.db import init_db
    init_db()


@pytest.fixture
def client():
    from denoiser.api.main import app
    return TestClient(app)


class TestBulkParsing:
    def test_alternating_action_and_source(self):
        raw = (
            b'{"index":{"_index":"logs"}}\n'
            b'{"message":"hello","level":"ERROR","service":"api","@timestamp":"2024-01-01T00:00:00Z"}\n'
            b'{"create":{}}\n'
            b'{"message":"two"}\n'
            b'{"delete":{"_id":"x"}}\n'
        )
        docs = parse_bulk(raw)
        assert len(docs) == 2  # delete has no source line
        assert docs[0]["message"] == "hello"
        assert docs[1]["message"] == "two"

    def test_update_unwraps_doc(self):
        raw = b'{"update":{"_id":"1"}}\n{"doc":{"message":"patched"}}\n'
        docs = parse_bulk(raw)
        assert docs == [{"message": "patched"}]

    def test_pipeline_resolvers_agree(self):
        raw = (
            b'{"index":{}}\n'
            b'{"message":"boom","level":"ERROR","service":"api","@timestamp":"2024-06-01T12:00:00Z"}\n'
        )
        doc = parse_bulk(raw)[0]
        assert resolve_source(doc) == "api"
        assert resolve_level(doc) == "ERROR"
        assert resolve_timestamp(doc).year == 2024


class TestHECParsing:
    def test_concatenated_events(self):
        raw = (
            b'{"event":{"message":"a","level":"WARN"},"time":1699999999,"sourcetype":"nginx"}'
            b'{"event":"plain string","host":"h1"}'
        )
        logs = parse_hec(raw)
        assert len(logs) == 2
        assert logs[0]["message"] == "a"
        assert logs[0]["timestamp"] == 1699999999
        assert logs[0]["source"] == "nginx"
        assert logs[1]["message"] == "plain string"
        assert logs[1]["source"] == "h1"

    def test_raw_endpoint_is_line_delimited(self):
        logs = parse_hec(b"line one\nline two\n", is_raw=True)
        assert [log["message"] for log in logs] == ["line one", "line two"]


class TestElasticEndpoint:
    def test_version_stub_for_preflight(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert res.json()["version"]["number"]  # Beats reads version.number

    def test_bulk_ingest(self, client):
        raw = (
            '{"index":{"_index":"logs"}}\n'
            '{"message":"one"}\n'
            '{"index":{"_index":"logs"}}\n'
            '{"message":"two"}\n'
        )
        res = client.post("/_bulk", content=raw, headers={"X-API-Key": API_KEY})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["errors"] is False
        assert len(body["items"]) == 2

    def test_indexed_bulk_path(self, client):
        raw = '{"index":{}}\n{"message":"x"}\n'
        res = client.post("/filebeat-2024/_bulk", content=raw, headers={"X-API-Key": API_KEY})
        assert res.status_code == 200
        assert len(res.json()["items"]) == 1

    def test_bulk_requires_auth(self, client):
        res = client.post("/_bulk", content='{"index":{}}\n{"m":"x"}\n')
        assert res.status_code == 401


class TestSplunkHEC:
    def test_event_ingest(self, client):
        res = client.post(
            "/services/collector",
            content='{"event":{"message":"hi"},"sourcetype":"app"}',
            headers={"Authorization": f"Splunk {API_KEY}"},
        )
        assert res.status_code == 200, res.text
        assert res.json() == {"text": "Success", "code": 0}

    def test_raw_ingest(self, client):
        res = client.post(
            "/services/collector/raw",
            content="raw line a\nraw line b\n",
            headers={"Authorization": f"Splunk {API_KEY}"},
        )
        assert res.status_code == 200
        assert res.json()["code"] == 0

    def test_health_is_unauthenticated(self, client):
        res = client.get("/services/collector/health")
        assert res.status_code == 200
        assert res.json()["code"] == 17

    def test_requires_splunk_token(self, client):
        res = client.post("/services/collector", content='{"event":"x"}')
        assert res.status_code == 401

    def test_rejects_bad_token(self, client):
        res = client.post(
            "/services/collector",
            content='{"event":"x"}',
            headers={"Authorization": "Splunk wrong-token"},
        )
        assert res.status_code == 401
