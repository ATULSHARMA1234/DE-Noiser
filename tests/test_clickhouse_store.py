"""Coverage for the module that owns every write and read of customer data.

`clickhouse_store` decides which rows are written, which tenant they are
written under, and which rows a query is allowed to see. It was the least
covered module in the package, which is the wrong way round: an untested tenant
predicate is a data-disclosure bug that no other test can catch, because every
caller passes through this one seam and would inherit the same mistake.

Two things are asserted throughout, and they are different:

* **Isolation.** Every read carries a bound tenant predicate, and there is no
  code path that produces a clause without one.
* **Failure semantics.** What each method does when ClickHouse is unreachable.
  Some return an empty result (a read the UI can render as "nothing"), some
  return False (a write the caller must retry), and one raises. Those choices
  are deliberate and a regression in any of them is silent.

The client is a recording fake. The value here is in what SQL and parameters
the store *builds*, which a real ClickHouse would only confirm at the cost of a
container per test run.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from denoiser.storage.clickhouse_store import (
    ClickHouseStore,
    _require_tenant,
    coerce_timestamp,
    resolve_level,
    resolve_source,
    resolve_timestamp,
)
from denoiser.storage.errors import StoreUnavailable


class FakeResult:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClient:
    """Records what it was asked to do; returns whatever the test seeds."""

    def __init__(self, rows=None, fail=False):
        self.rows = rows if rows is not None else []
        self.fail = fail
        self.queries: list[tuple[str, dict]] = []
        self.commands: list[tuple[str, dict]] = []
        self.inserts: list[tuple[str, list, list]] = []

    def query(self, sql, parameters=None):
        if self.fail:
            raise ConnectionError("clickhouse is down")
        self.queries.append((sql, parameters or {}))
        return FakeResult(self.rows)

    def command(self, sql, parameters=None):
        if self.fail:
            raise ConnectionError("clickhouse is down")
        self.commands.append((sql, parameters or {}))
        return None

    def insert(self, table, data, column_names=None):
        if self.fail:
            raise ConnectionError("clickhouse is down")
        self.inserts.append((table, data, column_names or []))
        return None


class DisconnectedClient:
    """Stands in for `self.client is None` without touching the network.

    `ClickHouseStore(client=None)` means "connect for me", so it cannot be used
    to build an unreachable store — the constructor would dial localhost:8123
    and the test would depend on nothing listening there.
    """


def store(client=None) -> ClickHouseStore:
    """A store over a fake client. Never connects."""
    built = ClickHouseStore(client=client or DisconnectedClient())
    if client is None:
        built.client = None
    return built


# ── The tenant predicate ─────────────────────────────────────────────────────

class TestNoQueryEscapesItsTenant:
    def test_scope_always_binds_a_tenant_predicate(self):
        where, params = store(FakeClient()).scope("7")
        assert "tenant_id = {tenant_id:String}" in where
        assert params["tenant_id"] == "7"

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_an_unscoped_query_is_refused_rather_than_run(self, empty):
        """Fail closed. A dropped predicate returns every customer's rows, and
        it would look exactly like a working query."""
        with pytest.raises(ValueError):
            store(FakeClient()).scope(empty)

    def test_an_integer_tenant_id_is_accepted(self):
        """Callers pass `Tenant.id`; the column is a String."""
        assert _require_tenant(7) == "7"

    def test_the_time_column_is_validated_not_interpolated(self):
        """It is the one part of the clause that cannot be a bound parameter,
        so it is checked against a fixed set instead of trusted."""
        with pytest.raises(ValueError, match="unknown time column"):
            store(FakeClient()).scope("1", time_column="timestamp; DROP TABLE")

    def test_both_real_time_columns_are_allowed(self):
        for column in ("timestamp", "start_time"):
            where, _ = store(FakeClient()).scope("1", from_ts=1000, time_column=column)
            assert column in where

    def test_time_bounds_are_bound_not_interpolated(self):
        where, params = store(FakeClient()).scope("1", from_ts=1_000, to_ts=2_000)
        assert "{from_ts:Float64}" in where
        assert "{to_ts:Float64}" in where
        assert params["from_ts"] == 1.0
        assert params["to_ts"] == 2.0

    def test_extra_clauses_bind_their_values(self):
        where, params = store(FakeClient()).scope(
            "1", extra=["source = {service:String}"], bind={"service": "checkout"}
        )
        assert "source = {service:String}" in where
        assert params["service"] == "checkout"


# ── Writes ───────────────────────────────────────────────────────────────────

class TestWrites:
    def test_insert_logs_writes_every_column_under_the_tenant(self):
        client = FakeClient()
        assert store(client).insert_logs([{"message": "boom", "level": "ERROR"}], tenant_id="4")

        table, data, columns = client.inserts[0]
        assert table == "semantic_logs"
        assert columns[0] == "tenant_id"
        assert data[0][0] == "4"
        assert len(data[0]) == len(columns)

    def test_insert_logs_refuses_an_unscoped_write(self):
        """The store will not create a partition no tenant can reach."""
        client = FakeClient()
        assert store(client).insert_logs([{"message": "x"}], tenant_id="") is False
        assert client.inserts == []

    def test_insert_logs_returns_false_rather_than_raising_when_down(self):
        """The ingestion worker treats False as 'retry'; an exception here would
        take down the consumer instead of retrying the batch."""
        assert store(FakeClient(fail=True)).insert_logs([{"message": "x"}], tenant_id="1") is False

    def test_insert_logs_with_no_client_is_a_failure_not_a_success(self):
        assert store(None).insert_logs([{"message": "x"}], tenant_id="1") is False

    def test_insert_traces_prepends_the_tenant_to_every_row(self):
        client = FakeClient()
        row = ("trace", "span", "parent", "svc", "op",
               datetime.now(UTC), datetime.now(UTC), 1.0, "OK", "{}", "[]")
        assert store(client).insert_traces([row], tenant_id="9")

        _table, data, columns = client.inserts[0]
        assert data[0][0] == "9"
        assert len(data[0]) == len(columns)

    def test_insert_traces_refuses_an_unscoped_write(self):
        client = FakeClient()
        assert store(client).insert_traces([("a",)], tenant_id="") is False
        assert client.inserts == []

    def test_insert_traces_returns_false_when_down(self):
        assert store(FakeClient(fail=True)).insert_traces([("a",)], tenant_id="1") is False


# ── Retention and offboarding ────────────────────────────────────────────────

class TestDeletes:
    def test_cleanup_scopes_the_delete_to_one_tenant(self):
        """An unscoped retention DELETE would erase every customer's history."""
        client = FakeClient()
        assert store(client).cleanup_old_data(tenant_id="3", days_to_keep=7)

        assert len(client.commands) == 2
        for sql, params in client.commands:
            assert "tenant_id = {tenant_id:String}" in sql
            assert params["tenant_id"] == "3"
            assert "INTERVAL 7 DAY" in sql

    def test_cleanup_casts_the_interval_to_an_integer(self):
        """`days` cannot be a bound parameter in an INTERVAL, so it is hard-cast
        rather than interpolated as given."""
        client = FakeClient()
        store(client).cleanup_old_data(tenant_id="3", days_to_keep="7; DROP TABLE x")  # type: ignore[arg-type]
        assert client.commands == []  # int() raised; nothing was executed

    def test_cleanup_refuses_without_a_tenant(self):
        client = FakeClient()
        assert store(client).cleanup_old_data(tenant_id="", days_to_keep=7) is False
        assert client.commands == []

    def test_cleanup_reports_failure_when_down(self):
        assert store(FakeClient(fail=True)).cleanup_old_data("1", 7) is False

    def test_delete_tenant_covers_both_tables(self):
        """Offboarding that misses a table leaves the customer's data behind,
        which is the GDPR Article 17 failure the purge exists to prevent."""
        client = FakeClient()
        assert store(client).delete_tenant("5")

        tables = {sql.split()[2] for sql, _ in client.commands}
        assert tables == {"semantic_logs", "semantic_traces"}
        for _sql, params in client.commands:
            assert params["tenant_id"] == "5"

    def test_delete_tenant_refuses_an_unscoped_purge(self):
        """Without the guard this deletes every tenant's rows."""
        client = FakeClient()
        assert store(client).delete_tenant("") is False
        assert client.commands == []

    def test_delete_tenant_reports_failure_rather_than_claiming_erasure(self):
        """A purge that reports success it did not achieve produces an erasure
        certificate for data that is still there."""
        assert store(FakeClient(fail=True)).delete_tenant("1") is False

    def test_delete_tenant_with_no_client_does_not_claim_success(self):
        assert store(None).delete_tenant("1") is False


# ── Reads ────────────────────────────────────────────────────────────────────

class TestReads:
    def test_query_logs_binds_the_tenant(self):
        client = FakeClient(rows=[])
        store(client).query_logs(tenant_id="2", limit=10)
        _sql, params = client.queries[0]
        assert params["tenant_id"] == "2"

    def test_query_logs_returns_empty_when_there_is_no_client(self):
        assert store(None).query_logs(tenant_id="1") == []

    def test_aggregate_metric_raises_instead_of_reporting_zero(self):
        """A metric that returns 0.0 when the store is unreachable is a number
        an SLO will treat as real, and a breach nobody can explain."""
        with pytest.raises(StoreUnavailable):
            store(None).aggregate_metric(tenant_id="1")

        with pytest.raises(StoreUnavailable):
            store(FakeClient(fail=True)).aggregate_metric(tenant_id="1")

    def test_aggregate_metric_returns_the_value(self):
        client = FakeClient(rows=[(42.0,)])
        assert store(client).aggregate_metric(tenant_id="1") == 42.0

    def test_aggregate_metric_treats_an_empty_result_as_zero(self):
        """No matching rows is a real answer of zero, unlike an unreachable
        store — which is why only one of the two raises."""
        assert store(FakeClient(rows=[])).aggregate_metric(tenant_id="1") == 0.0

    @pytest.mark.parametrize("aggregation", ["sum", "avg", "max", "min", "count"])
    def test_every_aggregation_binds_the_tenant(self, aggregation):
        client = FakeClient(rows=[(1.0,)])
        store(client).aggregate_metric(tenant_id="8", aggregation=aggregation)
        sql, params = client.queries[0]
        assert params["tenant_id"] == "8"
        assert "tenant_id = {tenant_id:String}" in sql

    def test_facets_degrade_to_empty_rather_than_failing_the_page(self):
        assert store(None).get_facets(tenant_id="1") == {"source": [], "level": []}
        assert store(FakeClient(fail=True)).get_facets(tenant_id="1") == {
            "source": [],
            "level": [],
        }

    def test_histogram_degrades_to_empty(self):
        assert store(None).get_histogram(tenant_id="1") == []
        assert store(FakeClient(fail=True)).get_histogram(tenant_id="1") == []

    def test_histogram_buckets_are_merged_by_timestamp(self):
        """Two levels in one bucket are one point with a total, not two points."""
        client = FakeClient(rows=[(1000, 3, "ERROR"), (1000, 2, "INFO")])
        buckets = store(client).get_histogram(tenant_id="1")
        assert len(buckets) == 1
        assert buckets[0]["count"] == 5

    @pytest.mark.parametrize(
        "span_hours,expected",
        [(0.5, "1 minute"), (12, "15 minute"), (48, "1 hour"), (24 * 30, "1 day")],
    )
    def test_histogram_interval_scales_with_the_window(self, span_hours, expected):
        """A month at one-minute resolution is 43,200 points the browser has to
        render; an hour at one-day resolution is a single bar."""
        client = FakeClient(rows=[])
        # A real millisecond epoch, not 0: `if from_ts and to_ts` treats zero as
        # "unset" and would silently fall back to the default interval.
        from_ts = 1_700_000_000_000
        to_ts = from_ts + int(span_hours * 3600 * 1000)
        store(client).get_histogram(tenant_id="1", from_ts=from_ts, to_ts=to_ts)
        assert f"INTERVAL {expected}" in client.queries[0][0]

    def test_available_reflects_whether_there_is_a_client(self):
        assert store(None).available is False
        assert store(FakeClient()).available is True


# ── Field resolution ─────────────────────────────────────────────────────────

class TestFieldResolution:
    def test_a_level_is_read_from_the_field_before_the_message(self):
        assert resolve_level({"level": "warn", "message": "ERROR everywhere"}) == "WARN"

    def test_a_missing_level_falls_back_to_the_message(self):
        assert resolve_level({"message": "ERROR: disk full"}) == "ERROR"

    def test_an_unclassifiable_line_defaults_to_info(self):
        assert resolve_level({"message": "started"}) == "INFO"

    def test_source_falls_back_when_absent(self):
        assert resolve_source({"source": "checkout"}) == "checkout"
        assert resolve_source({}) == "unknown"

    def test_a_boolean_is_not_an_epoch(self):
        """`bool` is a subclass of `int`; a truthy flag is not a timestamp."""
        assert coerce_timestamp(True) is None

    def test_an_unparseable_timestamp_becomes_now_rather_than_1970(self):
        """Epoch-zero rows sort to the beginning of every query and look like
        the oldest data in the system."""
        resolved = resolve_timestamp({"timestamp": "not a date"})
        assert (datetime.now(UTC) - resolved).total_seconds() < 60
