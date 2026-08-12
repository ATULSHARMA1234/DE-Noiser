"""Fail-closed tenant isolation at the ClickHouse store layer (audit finding H1).

Every tenant-scoped read/write must refuse to run when the tenant id is falsy
(``""``, ``None``, ``0``, ``"0"``). Previously the ``WHERE tenant_id = ...``
predicate was conditional (``if tenant_id:``), so an empty value silently ran
the query across every tenant — a cross-tenant data leak. These tests lock the
predicate in and prove the guard rejects empty tenants.
"""

import pytest

from denoiser.storage.clickhouse_store import ClickHouseStore, _require_tenant


class FakeResult:
    column_names: list[str] = []
    result_rows: list[tuple] = []


class RecordingClient:
    """Captures the SQL + bound params of every query/command/insert."""

    def __init__(self):
        self.queries: list[tuple[str, dict]] = []
        self.commands: list[tuple[str, dict]] = []
        self.inserts: list[tuple[str, list, list]] = []

    def query(self, sql, parameters=None):
        self.queries.append((sql, parameters or {}))
        return FakeResult()

    def command(self, sql, parameters=None):
        self.commands.append((sql, parameters or {}))

    def insert(self, table, data, column_names=None):
        self.inserts.append((table, data, column_names))


@pytest.fixture
def store(monkeypatch):
    # Skip the real clickhouse_connect client; inject a recorder.
    monkeypatch.setattr(ClickHouseStore, "_init_client", lambda self: None)
    s = ClickHouseStore()
    s.client = RecordingClient()
    return s


# --- The guard itself -------------------------------------------------------

@pytest.mark.parametrize("bad", ["", None, 0, "0", "   "])
def test_require_tenant_rejects_falsy(bad):
    with pytest.raises(ValueError, match="tenant_id is required"):
        _require_tenant(bad)


@pytest.mark.parametrize("good,expected", [(1, "1"), ("7", "7"), ("acme", "acme")])
def test_require_tenant_accepts_real(good, expected):
    assert _require_tenant(good) == expected


# --- Read paths must raise on empty tenant, never run unscoped --------------

def test_query_logs_empty_tenant_raises(store):
    with pytest.raises(ValueError):
        store.query_logs("level=ERROR", tenant_id="")
    assert store.client.queries == []  # nothing executed


def test_aggregate_metric_empty_tenant_raises(store):
    with pytest.raises(ValueError):
        store.aggregate_metric("level=ERROR", tenant_id=0)
    assert store.client.queries == []


def test_get_facets_empty_tenant_raises(store):
    with pytest.raises(ValueError):
        store.get_facets(tenant_id=None)
    assert store.client.queries == []


def test_get_histogram_empty_tenant_raises(store):
    with pytest.raises(ValueError):
        store.get_histogram("level=ERROR", tenant_id="0")
    assert store.client.queries == []


# --- Read paths always bind the tenant predicate when a tenant is given -----

def test_query_logs_binds_tenant_predicate(store):
    store.query_logs("level=ERROR", tenant_id=42)
    sql, params = store.client.queries[-1]
    assert "tenant_id = {tenant_id:String}" in sql
    assert params["tenant_id"] == "42"


def test_get_facets_binds_tenant_predicate(store):
    store.get_facets(tenant_id=42)
    # both source and level facet queries must carry the predicate
    assert len(store.client.queries) == 2
    for sql, params in store.client.queries:
        assert "tenant_id = {tenant_id:String}" in sql
        assert params["tenant_id"] == "42"


# --- Write paths fail-closed on empty tenant (no rows written) --------------

def test_insert_logs_empty_tenant_writes_nothing(store):
    ok = store.insert_logs([{"message": "hi"}], tenant_id="")
    assert ok is False
    assert store.client.inserts == []


def test_cleanup_old_data_empty_tenant_deletes_nothing(store):
    store.cleanup_old_data(tenant_id="", days_to_keep=30)
    assert store.client.commands == []
