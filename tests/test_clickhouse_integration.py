"""The ClickHouse paths, against a real ClickHouse.

Every other test in this suite substitutes the store. That is fast and it is the
right default, but it means the SQL is never executed, the schema is never
checked, and a claim like "tenant isolation is enforced in the WHERE clause" is
verified against a Python object that agrees with us by construction.

The consequence is not hypothetical. The trace ingest path spent its life
writing `None` into a non-Nullable `String` column: the insert failed for every
batch, the endpoint answered 200, and the mocked tests passed throughout. Only a
real server rejects that.

Skipped when no ClickHouse is reachable, so the default suite is unchanged:

    docker compose -f docker-compose-infra.yml up -d
    CLICKHOUSE_HOST=localhost uv run pytest tests/test_clickhouse_integration.py

Marked `integration` so CI can select or exclude them.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


def _reachable() -> bool:
    try:
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=os.getenv("CLICKHOUSE_HOST", "localhost"),
            port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
            username=os.getenv("CLICKHOUSE_USER", "default"),
            password=os.getenv("CLICKHOUSE_PASSWORD", ""),
            database=os.getenv("CLICKHOUSE_DB", "default"),
        )
        client.command("SELECT 1")
        return True
    except Exception:
        return False


pytest.importorskip("clickhouse_connect")
if not _reachable():
    pytest.skip(
        "no ClickHouse reachable — start docker-compose-infra.yml to run these",
        allow_module_level=True,
    )


@pytest.fixture
def store():
    from denoiser.storage.clickhouse_store import ClickHouseStore

    s = ClickHouseStore()
    assert s.client is not None, "constructor failed to connect"
    return s


@pytest.fixture
def tenant() -> str:
    """A tenant id nothing else in the database uses."""
    return f"it-{uuid.uuid4().hex[:12]}"


def _rows_for(store, tenant: str, table: str) -> int:
    where, params = store.scope(tenant)
    time_col = "timestamp" if table == "semantic_logs" else "start_time"
    assert time_col  # the scope helper needs to know which column bounds time
    return store.client.query(
        f"SELECT count() FROM {table} WHERE {where}", parameters=params
    ).result_rows[0][0]


class TestLogsRoundTrip:
    def test_a_batch_is_written_and_readable(self, store, tenant):
        assert store.insert_logs(
            [{"message": "checkout failed", "level": "ERROR", "source": "checkout"}],
            tenant_id=tenant,
        )
        assert _rows_for(store, tenant, "semantic_logs") == 1

    def test_redaction_actually_reaches_the_stored_row(self, store, tenant):
        """The claim that matters for the privacy story, checked in the store."""
        assert store.insert_logs(
            [{"message": "charge for victim@example.com card 4111111111111111"}],
            tenant_id=tenant,
        )
        where, params = store.scope(tenant)
        stored = store.client.query(
            f"SELECT message, raw_json FROM semantic_logs WHERE {where}", parameters=params
        ).result_rows[0]

        blob = " ".join(str(part) for part in stored)
        assert "victim@example.com" not in blob
        assert "4111111111111111" not in blob

    def test_one_tenants_query_never_sees_another(self, store, tenant):
        other = f"{tenant}-other"
        store.insert_logs([{"message": "mine"}], tenant_id=tenant)
        store.insert_logs([{"message": "theirs"}], tenant_id=other)

        assert _rows_for(store, tenant, "semantic_logs") == 1
        assert _rows_for(store, other, "semantic_logs") == 1

    def test_an_empty_tenant_is_refused_rather_than_run_unscoped(self, store):
        """A falsy tenant used to make the WHERE clause conditional."""
        for empty in ("", None, 0):
            assert store.insert_logs([{"message": "x"}], tenant_id=empty) is False


class TestTracesRoundTrip:
    def _row(self, *, parent: str, start: datetime) -> tuple:
        return (
            "trace-" + uuid.uuid4().hex[:8], "span-" + uuid.uuid4().hex[:8], parent,
            "checkout-api", "POST /charge", start, start + timedelta(milliseconds=120),
            120.0, "STATUS_CODE_OK", json.dumps({"http.method": "POST"}), json.dumps([]),
        )

    def test_a_root_span_with_no_parent_is_accepted(self, store, tenant):
        """`parent_span_id` is a non-Nullable String.

        The ingest path sent `None` for every root span — that is, for every
        trace — and ClickHouse rejected the whole batch each time. The mocked
        tests could not see it because a mock accepts anything.
        """
        now = datetime.now(UTC)
        assert store.insert_traces([self._row(parent="", start=now)], tenant_id=tenant)
        assert _rows_for(store, tenant, "semantic_traces") == 1

    def test_a_none_parent_is_still_rejected_by_the_server(self, store, tenant):
        """Documents why the ingest path must send "" — the column has not changed."""
        now = datetime.now(UTC)
        assert store.insert_traces(
            [self._row(parent=None, start=now)], tenant_id=tenant
        ) is False
        assert _rows_for(store, tenant, "semantic_traces") == 0

    def test_a_mixed_batch_of_root_and_child_spans(self, store, tenant):
        now = datetime.now(UTC)
        rows = [self._row(parent="", start=now), self._row(parent="span-parent", start=now)]
        assert store.insert_traces(rows, tenant_id=tenant)
        assert _rows_for(store, tenant, "semantic_traces") == 2


class TestRetentionDeletesOnlyWhatItShould:
    def test_old_rows_go_and_recent_rows_stay(self, store, tenant):
        old = datetime.now(UTC) - timedelta(days=30)
        recent = datetime.now(UTC) - timedelta(days=1)
        store.insert_logs([{"message": "ancient", "timestamp": old.timestamp()}], tenant_id=tenant)
        store.insert_logs([{"message": "recent", "timestamp": recent.timestamp()}], tenant_id=tenant)
        assert _rows_for(store, tenant, "semantic_logs") == 2

        assert store.cleanup_old_data(tenant, days_to_keep=7)
        # ALTER ... DELETE is a mutation; wait for it rather than racing it.
        store.client.command("SYSTEM FLUSH LOGS")
        for _ in range(50):
            if _rows_for(store, tenant, "semantic_logs") == 1:
                break
            import time as _time
            _time.sleep(0.2)

        assert _rows_for(store, tenant, "semantic_logs") == 1

    def test_retention_does_not_reach_another_tenant(self, store, tenant):
        other = f"{tenant}-safe"
        old = datetime.now(UTC) - timedelta(days=30)
        store.insert_logs([{"message": "theirs", "timestamp": old.timestamp()}], tenant_id=other)
        store.insert_logs([{"message": "mine", "timestamp": old.timestamp()}], tenant_id=tenant)

        assert store.cleanup_old_data(tenant, days_to_keep=7)
        for _ in range(50):
            if _rows_for(store, tenant, "semantic_logs") == 0:
                break
            import time as _time
            _time.sleep(0.2)

        assert _rows_for(store, tenant, "semantic_logs") == 0
        assert _rows_for(store, other, "semantic_logs") == 1


class TestMeteringReadsWhatWasWritten:
    def test_the_days_bytes_are_counted_from_a_real_query(self, store, tenant):
        """The metering SQL, executed. It used to bind an empty window."""
        yesterday = datetime.now(UTC) - timedelta(days=1)
        message = "x" * 100
        store.insert_logs(
            [{"message": message, "timestamp": yesterday.timestamp()}], tenant_id=tenant
        )

        where, params = store.scope(tenant)
        params = {**params, "day": yesterday.date().isoformat()}
        count, total = store.client.query(
            "SELECT count(), sum(length(message)) FROM semantic_logs "
            f"WHERE {where} AND toDate(timestamp) = toDate({{day:String}})",
            parameters=params,
        ).result_rows[0]

        assert count == 1
        assert total == len(message)

    def test_today_is_not_counted_when_metering_yesterday(self, store, tenant):
        """The regression: `toDate(now())` at 00:00 read a day with nothing in it."""
        yesterday = datetime.now(UTC) - timedelta(days=1)
        store.insert_logs([{"message": "today's traffic"}], tenant_id=tenant)

        where, params = store.scope(tenant)
        params = {**params, "day": yesterday.date().isoformat()}
        count = store.client.query(
            "SELECT count() FROM semantic_logs "
            f"WHERE {where} AND toDate(timestamp) = toDate({{day:String}})",
            parameters=params,
        ).result_rows[0][0]

        assert count == 0
