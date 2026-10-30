"""Every span must arrive owned by the customer that sent it.

`/v1/traces` had the tenant in hand — it authenticated with it and passed it to
ClickHouse — and did not put it on the relational row. `Span.tenant_id` is
nullable, so the omission was silent, and the consequences all pointed the same
way: metering counted the customer's traces as zero, offboarding did not delete
them, and a user belonging to no workspace could read them.

The archiver's *restore* path already carried a comment explaining this exact
failure and setting the column. The ingest path did not.
"""

import pytest
from fastapi.testclient import TestClient

from denoiser import runtime


def _payload(trace_id: str) -> dict:
    return {"resourceSpans": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "payments-api"}}
        ]},
        "scopeSpans": [{"spans": [
            {
                "traceId": trace_id,
                "spanId": trace_id[:16],
                "name": "POST /charge",
                "startTimeUnixNano": "1699999999000000000",
                "endTimeUnixNano": "1699999999250000000",
            },
            {
                "traceId": trace_id,
                "spanId": trace_id[16:32],
                "parentSpanId": trace_id[:16],
                "name": "INSERT payments",
                "startTimeUnixNano": "1699999999010000000",
                "endTimeUnixNano": "1699999999200000000",
            },
        ]}],
    }]}


class _AcceptingStore:
    client = object()

    def insert_traces(self, rows, tenant_id):
        self.tenant_id = tenant_id
        return True


class TestSpansAreAttributed:
    @pytest.fixture(scope="class", autouse=True)
    def _db(self):
        from denoiser.storage.db import init_db
        init_db()

    @pytest.fixture
    def client(self):
        from denoiser.api.main import app
        return TestClient(app)

    @pytest.fixture
    def tenant(self):
        """The workspace the static ingest key resolves to."""
        from denoiser.storage.db import SessionLocal, Tenant
        db = SessionLocal()
        try:
            return db.query(Tenant).order_by(Tenant.id).first().id
        finally:
            db.close()

    def test_an_ingested_span_carries_its_owner(self, client, tenant, monkeypatch):
        monkeypatch.setattr(runtime, "clickhouse_store", lambda: _AcceptingStore())
        from denoiser.storage.db import SessionLocal, Span

        trace_id = "aa11bb22cc33dd44ee55ff6677889900"
        res = client.post(
            "/v1/traces", json=_payload(trace_id),
            headers={"X-API-Key": "semanticos-ingest-key-123"},
        )
        assert res.status_code == 200, res.text

        db = SessionLocal()
        try:
            rows = db.query(Span).filter(Span.trace_id == trace_id).all()
            assert len(rows) == 2
            assert [r.tenant_id for r in rows] == [tenant, tenant]
            # The specific failure: nullable column, never set, nobody notices.
            assert None not in [r.tenant_id for r in rows]
        finally:
            db.close()

    def test_the_relational_and_columnar_owners_agree(self, client, tenant, monkeypatch):
        """ClickHouse was always given the tenant. Both stores must say the same."""
        store = _AcceptingStore()
        monkeypatch.setattr(runtime, "clickhouse_store", lambda: store)
        from denoiser.storage.db import SessionLocal, Span

        trace_id = "bb22cc33dd44ee55ff66778899001122"
        client.post(
            "/v1/traces", json=_payload(trace_id),
            headers={"X-API-Key": "semanticos-ingest-key-123"},
        )

        db = SessionLocal()
        try:
            row = db.query(Span).filter(Span.trace_id == trace_id).first()
            assert str(row.tenant_id) == str(store.tenant_id)
        finally:
            db.close()


class TestIngestAuthAlwaysYieldsANumericOwner:
    """The return type was `int` on one branch and `str` on two others.

    A string owner written to an integer column is how the rows above ended up
    unattributed, so the contract is asserted directly rather than only through
    its consequences.
    """

    @pytest.fixture(scope="class", autouse=True)
    def _db(self):
        from denoiser.storage.db import init_db
        init_db()

    def test_a_static_key_resolves_to_an_integer_tenant(self):
        from denoiser.api.auth import verify_ingest_auth
        from denoiser.storage.db import SessionLocal

        db = SessionLocal()
        try:
            resolved = verify_ingest_auth(
                api_key="semanticos-ingest-key-123", token=None, db=db
            )
        finally:
            db.close()

        assert isinstance(resolved, int)
        assert not isinstance(resolved, bool)

    def test_a_user_with_no_workspace_is_refused_rather_than_orphaned(self, monkeypatch):
        from fastapi import HTTPException

        from denoiser.api import auth as auth_module
        from denoiser.storage.db import SessionLocal, User

        db = SessionLocal()
        try:
            monkeypatch.setattr(
                auth_module, "get_current_user",
                lambda request, token, db: User(email="nobody@example.com", tenant_id=None),
            )
            with pytest.raises(HTTPException) as caught:
                auth_module.verify_ingest_auth(api_key=None, token="a-token", db=db)
        finally:
            db.close()

        # 503, not 401: the credential was valid. The shipper should hold the
        # batch and retry, not discard it as rejected.
        assert caught.value.status_code == 503


class TestMeteringSeesTheTraces:
    """The metering query counts spans by tenant, so attribution is what it reads.

    `billing_worker` carried a comment saying trace metering had been fixed from
    a hardcoded zero. It still returned zero, because the column it filters on
    was never populated.
    """

    @pytest.fixture(scope="class", autouse=True)
    def _db(self):
        from denoiser.storage.db import init_db
        init_db()

    def test_a_tenants_traces_are_counted(self, monkeypatch):
        from datetime import timedelta

        from denoiser.storage.db import SessionLocal, Span, Tenant
        from denoiser.utils.time import utcnow
        from denoiser.workers.billing_worker import aggregate_billing

        db = SessionLocal()
        try:
            tenant = db.query(Tenant).order_by(Tenant.id).first()
            when = utcnow().replace(hour=12) - timedelta(days=1)
            for n in range(3):
                db.add(Span(
                    tenant_id=tenant.id,
                    trace_id=f"metered-trace-{n}",
                    span_id=f"metered-span-{n}",
                    service_name="billing-fixture",
                    operation_name="work",
                    start_time=when,
                    end_time=when,
                    duration_ms=1.0,
                ))
            db.commit()

            class _Store:
                available = True
                client = None

                def cleanup_old_data(self, *a, **k):
                    return True

            monkeypatch.setattr(
                "denoiser.workers.billing_worker.runtime.clickhouse_store", lambda: _Store()
            )
            summary = aggregate_billing(db=db, day=when.date(), enforce_retention=False)
        finally:
            db.close()

        assert summary["traces"] >= 3, summary
