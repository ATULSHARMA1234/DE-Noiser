"""Redaction is a property of ingest, not of one router.

`/ingest` redacted at its boundary. `/v1/logs` — the OTLP endpoint, the one the
README points enterprises at — did not, and neither did the Kafka consumer. So a
customer following the documented integration wrote email addresses and bearer
tokens verbatim into ClickHouse, into the raw object-store copy, and onto the
Redis stream the live console renders, for their full retention period.

Nobody decided that. It was one rule written in one place and not the others,
which is the same failure this codebase already corrected for tenant scoping.
The parity tests below are the point: every entrance is checked, together, so a
new one cannot quietly be added without one.
"""

import json

import pytest
from fastapi.testclient import TestClient

from denoiser import runtime

SECRET_EMAIL = "victim.person@customer-example.com"
SECRET_CARD = "4111111111111111"


class _CapturingStore:
    """Captures what would have been written, exactly as the store received it."""

    client = object()

    def __init__(self):
        self.logs: list[dict] = []

    def insert_logs(self, logs, tenant_id, redact=True):
        # Mirrors the real signature: if a caller says it already redacted, this
        # is what the store would persist.
        if redact:
            from denoiser.api.platform_settings import redact_batch
            logs = redact_batch(logs)
        self.logs.extend(logs)
        return True

    def insert_traces(self, rows, tenant_id):
        return True


class _CapturingSink:
    def __init__(self):
        self.written: list[str] = []

    def write(self, tenant_id, serialized):
        self.written.extend(serialized)


@pytest.fixture(scope="module", autouse=True)
def _db():
    from denoiser.storage.db import init_db
    init_db()


@pytest.fixture(scope="module")
def client():
    from denoiser.api.main import app
    return TestClient(app)


@pytest.fixture
def store(monkeypatch):
    s = _CapturingStore()
    monkeypatch.setattr(runtime, "clickhouse_store", lambda: s)
    return s


@pytest.fixture
def sink(monkeypatch):
    s = _CapturingSink()
    monkeypatch.setattr(runtime, "raw_log_sink", lambda: s)
    monkeypatch.setattr(
        "denoiser.api.platform_settings.raw_log_storage_enabled", lambda: True
    )
    return s


def _auth():
    return {"X-API-Key": "semanticos-ingest-key-123"}


def _leaked(blob: str) -> bool:
    return SECRET_EMAIL in blob or SECRET_CARD in blob


class TestEveryIngestPathRedacts:
    def test_the_plain_ingest_endpoint(self, client, store):
        res = client.post(
            "/ingest",
            json={"logs": [{"message": f"charge failed for {SECRET_EMAIL} card {SECRET_CARD}"}]},
            headers=_auth(),
        )
        assert res.status_code in (200, 202), res.text
        assert not _leaked(json.dumps(store.logs)), store.logs

    def test_the_otlp_endpoint(self, client, store):
        """The regression test. This path stored the record verbatim."""
        payload = {"resourceLogs": [{
            "resource": {"attributes": [
                {"key": "service.name", "value": {"stringValue": "billing"}}
            ]},
            "scopeLogs": [{"logRecords": [{
                "timeUnixNano": "1699999999000000000",
                "body": {"stringValue": f"charge failed for {SECRET_EMAIL} card {SECRET_CARD}"},
            }]}],
        }]}

        res = client.post("/v1/logs", json=payload, headers=_auth())

        assert res.status_code == 200, res.text
        assert not _leaked(json.dumps(store.logs)), store.logs

    def test_the_otlp_endpoint_does_not_leak_into_the_raw_copy(self, client, store, sink):
        """The object-store copy is a sink too, and it is the long-lived one."""
        payload = {"resourceLogs": [{
            "resource": {"attributes": []},
            "scopeLogs": [{"logRecords": [{
                "timeUnixNano": "1699999999000000000",
                "body": {"stringValue": f"contact {SECRET_EMAIL}"},
            }]}],
        }]}

        client.post("/v1/logs", json=payload, headers=_auth())

        assert sink.written, "the raw sink was not exercised"
        assert not _leaked("".join(sink.written)), sink.written

    def test_the_store_redacts_on_its_own_for_callers_that_did_not(self):
        """The backstop, for the Kafka consumer and anything added later.

        The consumer writes to ClickHouse without passing through a router, so
        the boundary helper never sees its records.
        """
        from denoiser.storage.clickhouse_store import ClickHouseStore

        captured = []

        class _Client:
            def insert(self, table, data, column_names):
                captured.extend(data)

        store = ClickHouseStore.__new__(ClickHouseStore)
        store.client = _Client()

        assert store.insert_logs(
            [{"message": f"user {SECRET_EMAIL} paid with {SECRET_CARD}"}], tenant_id="1"
        )
        assert captured
        assert not _leaked(json.dumps(captured, default=str)), captured


class TestRedactionCoversTheWholeRecord:
    def test_not_only_the_message_field(self, client, store):
        """`raw_json` is stored alongside `message` and is searchable.

        Redacting one and not the other leaves the value exactly where a query
        would find it.
        """
        res = client.post(
            "/ingest",
            json={"logs": [{
                "message": "payment declined",
                "user": {"email": SECRET_EMAIL},
                "tags": [f"card:{SECRET_CARD}"],
            }]},
            headers=_auth(),
        )
        assert res.status_code in (200, 202), res.text
        assert not _leaked(json.dumps(store.logs)), store.logs
