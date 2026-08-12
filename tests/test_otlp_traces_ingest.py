"""OTLP trace ingestion: what a root span writes, and what a refused store returns.

Every trace has exactly one root span, and a root span has no parent. That value
reached ClickHouse as `None` against a non-Nullable `String` column, so the
insert failed for the whole batch — which meant no trace was ever stored, while
the endpoint answered `200 {"status": "success"}` and the listing answered `[]`
without erroring. The two halves are tested separately because either one alone
still loses the batch silently.
"""

import pytest
from fastapi.testclient import TestClient

from denoiser import runtime


def _payload(with_parent: bool = True) -> dict:
    spans = [{
        "traceId": "5b8efff798038103d269b633813fc60c",
        "spanId": "eee19b7ec3c1b174",
        "name": "POST /checkout",
        "startTimeUnixNano": "1699999999000000000",
        "endTimeUnixNano": "1699999999120000000",
    }]
    if with_parent:
        spans.append({
            "traceId": "5b8efff798038103d269b633813fc60c",
            "spanId": "eee19b7ec3c1b175",
            "parentSpanId": "eee19b7ec3c1b174",
            "name": "SELECT orders",
            "startTimeUnixNano": "1699999999000000000",
            "endTimeUnixNano": "1699999999090000000",
        })
    return {"resourceSpans": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "checkout-api"}}
        ]},
        "scopeSpans": [{"spans": spans}],
    }]}


class _RecordingStore:
    """Stands in for the ClickHouse store, capturing the rows it is handed."""

    def __init__(self, accept: bool = True):
        self.client = object()  # truthy: "ClickHouse is configured"
        self.accept = accept
        self.rows: list[tuple] = []

    def insert_traces(self, rows, tenant_id):
        self.rows.extend(rows)
        return self.accept


class TestOtlpTraceIngest:
    @pytest.fixture(scope="class", autouse=True)
    def _db(self):
        from denoiser.storage.db import init_db
        init_db()

    @pytest.fixture
    def client(self):
        from denoiser.api.main import app
        return TestClient(app)

    def _auth(self):
        return {"X-API-Key": "semanticos-ingest-key-123"}

    def test_root_span_is_written_with_an_empty_parent_not_none(self, client, monkeypatch):
        store = _RecordingStore()
        monkeypatch.setattr(runtime, "clickhouse_store", lambda: store)

        res = client.post("/v1/traces", json=_payload(), headers=self._auth())

        assert res.status_code == 200, res.text
        assert res.json()["clickhouse"] is True
        assert len(store.rows) == 2

        # Column order is (trace_id, span_id, parent_span_id, ...).
        parents = [row[2] for row in store.rows]
        assert parents == ["", "eee19b7ec3c1b174"]
        assert None not in parents

    def test_a_refused_batch_is_not_reported_as_success(self, client, monkeypatch):
        store = _RecordingStore(accept=False)
        monkeypatch.setattr(runtime, "clickhouse_store", lambda: store)

        res = client.post("/v1/traces", json=_payload(), headers=self._auth())

        # 503, so the exporter still holds the batch and retries it. A 200 here
        # told it the spans were delivered and it dropped them.
        assert res.status_code == 503, res.text
        assert "retry" in res.json()["detail"].lower()

    def test_no_clickhouse_configured_is_still_a_success(self, client, monkeypatch):
        class _Unconfigured:
            client = None

        monkeypatch.setattr(runtime, "clickhouse_store", lambda: _Unconfigured())

        res = client.post("/v1/traces", json=_payload(), headers=self._auth())

        # Nothing was refused — there is no store. The spans are in the
        # relational database and the listing falls back to it.
        assert res.status_code == 200, res.text
        assert res.json()["clickhouse"] is False
        assert res.json()["spans_ingested"] == 2


class TestARetriedBatchDoesNotAccumulate:
    """A 503 tells the exporter to resend. Resending must not duplicate rows.

    The endpoint used to commit its relational rows *before* it knew whether the
    trace store had taken the batch, so each retry of a failed batch committed
    another full copy. Over a ClickHouse outage — six or so retries per batch,
    with default OTLP backoff — that is unbounded growth in the table the
    archiver later loads into memory.
    """

    @pytest.fixture(scope="class", autouse=True)
    def _db(self):
        from denoiser.storage.db import init_db
        init_db()

    @pytest.fixture
    def client(self):
        from denoiser.api.main import app
        return TestClient(app)

    def _auth(self):
        return {"X-API-Key": "semanticos-ingest-key-123"}

    def _stored(self, trace_id: str) -> int:
        from denoiser.storage.db import SessionLocal, Span
        db = SessionLocal()
        try:
            return db.query(Span).filter(Span.trace_id == trace_id).count()
        finally:
            db.close()

    def test_a_refused_batch_writes_no_relational_rows(self, client, monkeypatch):
        store = _RecordingStore(accept=False)
        monkeypatch.setattr(runtime, "clickhouse_store", lambda: store)
        trace_id = _payload()["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"]
        before = self._stored(trace_id)

        res = client.post("/v1/traces", json=_payload(), headers=self._auth())

        assert res.status_code == 503, res.text
        # The store is the system of record and it refused. Nothing should have
        # been committed anywhere for the caller to have to reconcile later.
        assert self._stored(trace_id) == before

    def test_replaying_an_accepted_batch_is_a_no_op(self, client, monkeypatch):
        store = _RecordingStore(accept=True)
        monkeypatch.setattr(runtime, "clickhouse_store", lambda: store)
        trace_id = _payload()["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"]

        first = client.post("/v1/traces", json=_payload(), headers=self._auth())
        assert first.status_code == 200, first.text
        after_first = self._stored(trace_id)
        assert after_first >= 2

        # The same batch again — what an exporter sends when our 200 was lost in
        # transit. Uniqueness of (tenant, trace, span) has to absorb it.
        second = client.post("/v1/traces", json=_payload(), headers=self._auth())

        assert second.status_code == 200, second.text
        assert self._stored(trace_id) == after_first
        # The batch is still fully accepted — the exporter did nothing wrong —
        # but none of it was new.
        assert second.json()["spans_ingested"] == 2
        assert second.json()["spans_stored"] == 0
