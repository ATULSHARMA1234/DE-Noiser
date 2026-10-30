"""`GET /traces` and `GET /traces/{id}` on the SQLite path: scoped, and honest
about failure.

Both endpoints swallowed every ClickHouse error and, if the fallback failed
too, answered `[]` or 404 — an outage looked exactly like an empty account.
"""

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from denoiser import runtime
from denoiser.storage.db import SessionLocal, Span, User, init_db


class _NoClickHouse:
    client = None


@pytest.fixture
def client(monkeypatch):
    from denoiser.api.auth import get_current_user
    from denoiser.api.main import app

    init_db()
    monkeypatch.setattr(runtime, "clickhouse_store", lambda: _NoClickHouse())
    app.dependency_overrides[get_current_user] = lambda: User(id=1, email="t@x.io", role="VIEWER", tenant_id=1)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def spans():
    """One trace for tenant 1 and one for tenant 2, removed afterwards."""
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    mine, theirs = uuid.uuid4().hex, uuid.uuid4().hex
    db = SessionLocal()
    rows = [
        Span(tenant_id=1, trace_id=mine, span_id="a1", parent_span_id=None, service_name="checkout",
             operation_name="POST /pay", start_time=now, end_time=now + dt.timedelta(milliseconds=120),
             duration_ms=120, status_code="ERROR"),
        Span(tenant_id=1, trace_id=mine, span_id="a2", parent_span_id="a1", service_name="db",
             operation_name="SELECT", start_time=now, end_time=now + dt.timedelta(milliseconds=40),
             duration_ms=40, status_code="OK"),
        Span(tenant_id=2, trace_id=theirs, span_id="b1", parent_span_id=None, service_name="other",
             operation_name="GET /", start_time=now, end_time=now, duration_ms=0, status_code="OK"),
    ]
    db.add_all(rows)
    db.commit()
    try:
        yield mine, theirs
    finally:
        for r in rows:
            db.delete(r)
        db.commit()
        db.close()


def test_listing_shows_only_the_callers_traces(client, spans):
    mine, theirs = spans
    ids = {t["trace_id"] for t in client.get("/traces", params={"limit": 1000}).json()}
    assert mine in ids
    assert theirs not in ids


def test_trace_detail_carries_root_and_errors(client, spans):
    mine, _ = spans
    body = client.get(f"/traces/{mine}").json()
    assert body["root_service"] == "checkout"
    assert body["span_count"] == 2
    assert body["error_count"] == 1


def test_another_tenants_trace_is_not_found(client, spans):
    _, theirs = spans
    assert client.get(f"/traces/{theirs}").status_code == 404


def test_storage_failure_is_503_not_an_empty_list(client, monkeypatch):
    from denoiser.api.scope import TenantScope

    def broken(self, model):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(TenantScope, "predicate", broken)
    assert client.get("/traces").status_code == 503
