"""Metric rules: CRUD, roles, validation, and the extractor's real aggregates.

The metric-rules API had no test. The extractor once multiplied a random number
into every datapoint; these pin that what it stores is what the log store
returned for that rule's own tenant and window, and that a store outage is a
gap in the series rather than a zero.
"""

import datetime as dt

import pytest

from denoiser.storage.db import ExtractedMetric, MetricRule, SessionLocal


def _rule(client, **overrides):
    body = {"name": "5xx", "query": "status:>=500", "aggregation": "count", "window_seconds": 120, **overrides}
    return client.post("/metrics/rules", json=body)


class TestRules:
    def test_create_list_delete(self, two_orgs):
        a, _, as_user = two_orgs
        client = as_user(a)
        created = _rule(client).json()
        assert created["tenant_id"] == a and created["enabled"] is True

        assert [r["id"] for r in client.get("/metrics/rules").json()] == [created["id"]]
        assert client.delete(f"/metrics/rules/{created['id']}").json() == {"status": "deleted"}
        assert client.get("/metrics/rules").json() == []

    def test_malformed_query_is_refused_when_saved(self, two_orgs):
        a, _, as_user = two_orgs
        res = _rule(as_user(a), query="(status:>=500")
        assert res.status_code == 422
        assert "never closed" in res.text

    @pytest.mark.parametrize("role,create,delete", [("VIEWER", 403, 403), ("ANALYST", 200, 403), ("ADMIN", 200, 200)])
    def test_roles(self, two_orgs, role, create, delete):
        a, _, as_user = two_orgs
        existing = _rule(as_user(a)).json()["id"]
        client = as_user(a, role=role)
        assert _rule(client).status_code == create
        assert client.delete(f"/metrics/rules/{existing}").status_code == delete

    def test_another_organisations_rule_is_invisible(self, two_orgs):
        a, b, as_user = two_orgs
        rule_id = _rule(as_user(a)).json()["id"]
        theirs = as_user(b)
        assert theirs.get("/metrics/rules").json() == []
        assert theirs.get(f"/metrics/rules/{rule_id}/data").status_code == 404
        assert theirs.delete(f"/metrics/rules/{rule_id}").status_code == 404


class TestData:
    def test_series_is_the_rules_own_samples_oldest_first(self, two_orgs):
        a, b, as_user = two_orgs
        client = as_user(a)
        rule_id = _rule(client).json()["id"]
        now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        db = SessionLocal()
        try:
            db.add_all([
                ExtractedMetric(tenant_id=a, rule_id=rule_id, timestamp=now, value=7),
                ExtractedMetric(tenant_id=a, rule_id=rule_id, timestamp=now - dt.timedelta(minutes=1), value=3),
                # Same rule id, wrong owner: must not be served.
                ExtractedMetric(tenant_id=b, rule_id=rule_id, timestamp=now, value=999),
            ])
            db.commit()
        finally:
            db.close()

        body = client.get(f"/metrics/rules/{rule_id}/data").json()
        assert body["rule_name"] == "5xx"
        assert [p["value"] for p in body["data"]] == [3, 7]


class _Store:
    """Stands in for ClickHouse; answers per tenant and records what it was asked."""

    def __init__(self, unavailable=False):
        self.unavailable = unavailable
        self.calls = []

    def aggregate_metric(self, query, aggregation="count", tenant_id=None, from_ts=None, to_ts=None):
        from denoiser.storage.errors import StoreUnavailable

        self.calls.append({"query": query, "aggregation": aggregation, "tenant_id": tenant_id,
                           "window_ms": to_ts - from_ts})
        # Rules belonging to anyone else (a shared dev database has real ones)
        # get the outage answer, so the extractor writes nothing for them.
        if self.unavailable or tenant_id not in self.owner:
            raise StoreUnavailable("clickhouse down")
        return {"a": 11.0, "b": 22.0}[self.owner[tenant_id]]


class TestExtractor:
    @pytest.fixture
    def rules(self, two_orgs):
        a, b, _ = two_orgs
        db = SessionLocal()
        try:
            ra = MetricRule(tenant_id=a, name="a", query="level:ERROR", aggregation="count", window_seconds=120)
            rb = MetricRule(tenant_id=b, name="b", query="status:>=500", aggregation="avg", window_seconds=60)
            db.add_all([ra, rb])
            db.commit()
            return a, b, ra.id, rb.id
        finally:
            db.close()

    def _run(self, monkeypatch, store, owner):
        from denoiser import runtime
        from denoiser.workers.analysis_worker import extract_metrics

        store.owner = owner
        monkeypatch.setattr(runtime, "clickhouse_store", lambda: store)
        extract_metrics()

    def _samples(self, rule_id):
        db = SessionLocal()
        try:
            return [(m.tenant_id, m.value) for m in db.query(ExtractedMetric).filter(ExtractedMetric.rule_id == rule_id)]
        finally:
            db.close()

    def test_each_rule_stores_its_own_tenants_aggregate(self, rules, monkeypatch):
        a, b, ra, rb = rules
        store = _Store()
        self._run(monkeypatch, store, owner={a: "a", b: "b"})

        assert self._samples(ra) == [(a, 11.0)]
        assert self._samples(rb) == [(b, 22.0)]
        asked = {c["tenant_id"]: c for c in store.calls if c["tenant_id"] in (a, b)}
        assert asked[a]["query"] == "level:ERROR" and asked[a]["window_ms"] == 120_000
        assert asked[b]["aggregation"] == "avg" and asked[b]["window_ms"] == 60_000

    def test_store_outage_is_a_gap_not_a_zero(self, rules, monkeypatch):
        a, b, ra, rb = rules
        self._run(monkeypatch, _Store(unavailable=True), owner={a: "a", b: "b"})
        assert self._samples(ra) == [] and self._samples(rb) == []
