"""The archival job moves cold rows out of the hot stores, and it deletes.

Nothing covered it before, which is a poor combination with a `DELETE` whose
predicate was built by string formatting and a restore that assigned orphaned
rows to a live organisation.
"""

import gzip
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from denoiser.storage import archiver
from denoiser.storage.archiver import UNKNOWN_TENANT, S3ArchiverEngine


@pytest.fixture(autouse=True)
def _schema():
    """`run_archival` sweeps the spans table first; it has to exist."""
    from denoiser.storage.db import init_db

    init_db()


def _store(rows):
    """A ClickHouse store whose SELECT returns ``rows``."""
    store = MagicMock()
    result = MagicMock()
    result.column_names = ["tenant_id", "timestamp", "source", "level", "message", "raw_json"]
    result.result_rows = rows
    store.client.query.return_value = result
    return store


class TestPruning:
    def test_the_cutoff_is_bound_not_formatted_into_the_statement(self):
        old = datetime(2026, 1, 1, tzinfo=UTC)
        store = _store([["7", old, "api", "ERROR", "boom", "{}"]])

        with patch.object(archiver.runtime, "clickhouse_store", return_value=store), \
             patch("denoiser.api.platform_settings.load_settings", return_value={"s3_archive_days": 7}):
            S3ArchiverEngine.run_archival()

        select_sql = store.client.query.call_args.args[0]
        assert "{cutoff:Float64}" in select_sql
        assert "2026-01-01" not in select_sql

        delete_sql = store.client.command.call_args.args[0]
        assert "{newest:Float64}" in delete_sql
        assert "2026-01-01" not in delete_sql, "no literal timestamp should reach the DELETE"

    def test_the_delete_reaches_only_as_far_as_the_rows_written_out(self):
        """Re-using the cutoff would drop rows that arrived after the SELECT.

        Backfilled logs are old by definition, so a row landing mid-job sits
        below the cutoff and would be deleted without ever being archived.
        """
        oldest = datetime(2026, 1, 1, tzinfo=UTC)
        newest = datetime(2026, 1, 3, tzinfo=UTC)
        store = _store([
            ["7", oldest, "api", "ERROR", "a", "{}"],
            ["7", newest, "api", "ERROR", "b", "{}"],
        ])

        with patch.object(archiver.runtime, "clickhouse_store", return_value=store), \
             patch("denoiser.api.platform_settings.load_settings", return_value={"s3_archive_days": 7}):
            S3ArchiverEngine.run_archival()

        bound = store.client.command.call_args.kwargs["parameters"]["newest"]
        assert bound == newest.timestamp()

    def test_nothing_is_deleted_when_nothing_was_archived(self):
        store = _store([])

        with patch.object(archiver.runtime, "clickhouse_store", return_value=store), \
             patch("denoiser.api.platform_settings.load_settings", return_value={"s3_archive_days": 7}):
            S3ArchiverEngine.run_archival()

        store.client.command.assert_not_called()


class TestRehydration:
    def test_a_log_with_no_recorded_owner_is_restored_unattributed(self, tmp_path):
        """It used to be restored as tenant "default" — a real organisation."""
        name = f"logs_t{UNKNOWN_TENANT}_{int(datetime.now(UTC).timestamp())}.jsonl.gz"
        path = archiver.ARCHIVE_DIR / name
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
                "source": "api", "level": "ERROR", "message": "orphan", "raw_json": "{}",
            }) + "\n")

        store = MagicMock()
        try:
            with patch.object(archiver.runtime, "clickhouse_store", return_value=store), \
                 patch("denoiser.api.platform_settings.load_settings", return_value={}):
                result = S3ArchiverEngine.hydrate_archive(name)
        finally:
            path.unlink(missing_ok=True)

        assert result["status"] == "success"
        assert store.insert_logs.call_args.kwargs["tenant_id"] == UNKNOWN_TENANT

    def test_a_log_keeps_the_owner_it_was_archived_under(self, tmp_path):
        name = f"logs_t9_{int(datetime.now(UTC).timestamp())}.jsonl.gz"
        path = archiver.ARCHIVE_DIR / name
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(json.dumps({
                "tenant_id": "9",
                "timestamp": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
                "source": "api", "level": "INFO", "message": "theirs", "raw_json": "{}",
            }) + "\n")

        store = MagicMock()
        try:
            with patch.object(archiver.runtime, "clickhouse_store", return_value=store), \
                 patch("denoiser.api.platform_settings.load_settings", return_value={}):
                S3ArchiverEngine.hydrate_archive(name)
        finally:
            path.unlink(missing_ok=True)

        assert store.insert_logs.call_args.kwargs["tenant_id"] == "9"
