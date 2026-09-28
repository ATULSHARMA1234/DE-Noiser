"""One module knows how `semantic_logs` is partitioned. Everything else asks it.

`tests/test_tenancy_conformance.py` polices the SQLAlchemy layer — a model with
a `tenant_id` cannot ship without a scoping test. Nothing equivalent existed for
the raw-ClickHouse layer, which is precisely where the isolation defects were:
the SLO engine measured every organisation's traffic against one objective, and
the archiver read and deleted across all of them, because each had typed out its
own window and one of them left the tenant predicate out.

The structural tests below are the missing half of that pair.
"""

from __future__ import annotations

import pathlib

import pytest

from denoiser import runtime
from denoiser.storage.clickhouse_store import ClickHouseStore

SRC = pathlib.Path(runtime.__file__).parent

#: The store owns the tenant predicate. The archiver is the one module whose job
#: is deliberately deployment-wide — it sweeps every organisation's cold rows —
#: so it names the table without scoping to a tenant, on purpose.
PREDICATE_OWNERS = {
    "storage/clickhouse_store.py",
    "storage/archiver.py",
}


def _sources():
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path, path.relative_to(SRC).as_posix(), path.read_text(encoding="utf-8")


class TestOnlyOneModuleConstructsTheStore:
    def test_every_caller_goes_through_the_runtime_seam(self):
        """`ClickHouseStore()` was constructed at twelve sites, eleven of which
        bypassed the seam — so substituting it in a test meant patching eleven
        module paths, and two of the twelve re-issued the schema DDL on a
        schedule."""
        offenders = [
            rel for path, rel, text in _sources()
            if "ClickHouseStore()" in text and rel != "runtime.py"
        ]
        assert not offenders, (
            f"these modules construct their own store instead of asking "
            f"denoiser.runtime for it: {offenders}"
        )

    def test_importing_a_router_opens_no_socket(self):
        """`api/query.py` held a module-scope store, so importing the router
        connected and ran both `CREATE TABLE` statements as an import side
        effect."""
        for _, rel, text in _sources():
            if not rel.startswith("api/"):
                continue
            for line in text.splitlines():
                assert not line.startswith("clickhouse_store = "), (
                    f"{rel} builds a store at module scope"
                )


class TestOnlyTheStoreWritesTheTenantPredicate:
    """Querying the tables from elsewhere is fine. Writing the predicate is not.

    The predicate is the part that was wrong: it was hand-written at seven
    sites, and left out entirely at three. A module may still assemble a
    specialised statement — the SLO engine's latency `countIf` has no business
    inside the store — as long as its WHERE clause comes from `scope()`.
    """

    def test_nobody_hand_writes_the_tenant_clause(self):
        offenders = sorted(
            rel for path, rel, text in _sources()
            if rel not in PREDICATE_OWNERS and "tenant_id = {tenant_id" in text
        )
        assert not offenders, (
            f"these modules write the tenant predicate themselves, so it is "
            f"theirs to get wrong: {offenders}. Use ClickHouseStore.scope()."
        )

    def test_anything_querying_the_tables_goes_through_scope(self):
        offenders = sorted(
            rel for path, rel, text in _sources()
            if rel not in PREDICATE_OWNERS
            and ("FROM semantic_logs" in text or "FROM semantic_traces" in text)
            and ".scope(" not in text
        )
        assert not offenders, (
            f"these modules query the tenant-partitioned tables without asking "
            f"the store for a scoped WHERE clause: {offenders}"
        )


class TestScopeIsFailClosed:
    """`scope()` is the only way to obtain a WHERE clause, and it demands a tenant."""

    @pytest.fixture()
    def store(self):
        return ClickHouseStore(client=object())

    @pytest.mark.parametrize("empty", [None, "", 0, "0", "   "])
    def test_an_empty_tenant_is_refused_not_dropped(self, store, empty):
        """The predicate used to be conditional on a truthy tenant, so an empty
        one silently ran the query across every organisation."""
        with pytest.raises(ValueError, match="unscoped"):
            store.scope(empty)

    def test_the_tenant_predicate_is_always_present(self, store):
        where, params = store.scope(7)
        assert where.startswith("tenant_id = {tenant_id:String}")
        assert params["tenant_id"] == "7"

    def test_extra_clauses_are_anded_never_interpolated(self, store):
        where, params = store.scope(
            7, extra=["source = {service:String}"], bind={"service": "api'; DROP"}
        )
        assert where == "tenant_id = {tenant_id:String} AND source = {service:String}"
        assert params["service"] == "api'; DROP"
        assert "DROP" not in where

    def test_time_bounds_are_bound_values(self, store):
        where, params = store.scope(7, from_ts=1_000, to_ts=2_000)
        assert "{from_ts:Float64}" in where and "{to_ts:Float64}" in where
        assert params["from_ts"] == 1.0
        assert params["to_ts"] == 2.0

    def test_lql_is_compiled_in_not_concatenated_raw(self, store):
        where, _params = store.scope(7, query_string="level:ERROR")
        assert where.startswith("tenant_id = {tenant_id:String} AND (")
        assert "ERROR" not in where, "the value should be bound, not inlined"


class TestTheStoreTakesItsClient:
    def test_a_supplied_client_means_no_connection_attempt(self):
        """Tests used to monkeypatch the private `_init_client` to stop the
        constructor dialling out, which coupled every one of them to a private
        method's name."""
        sentinel = object()
        store = ClickHouseStore(client=sentinel)
        assert store.client is sentinel
        assert store.available

    def test_substitution_is_one_call_for_every_caller(self):
        substitute = ClickHouseStore(client=object())
        runtime.set_clickhouse_store(substitute)
        try:
            assert runtime.clickhouse_store() is substitute
        finally:
            runtime.reset()
