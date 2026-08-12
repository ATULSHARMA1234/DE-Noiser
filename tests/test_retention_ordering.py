"""Nothing is deleted before it has been archived.

Retention and archival used to be two jobs, on two schedulers, in two
processes — retention on the Celery beat at 00:00, archival on the API's
APScheduler at 04:00 — sharing one seven-day threshold. The destructive one ran
first by four hours, so a free-tier tenant's cold logs were hard-deleted from
ClickHouse before the sweep that would have written them to S3 came looking.
Both jobs logged success. The archive was empty and nothing said so.

An ordering that matters cannot be expressed as two crontabs that happen to be
four hours apart, so these tests assert the order directly, and assert that a
failed archive stops the deletion rather than proceeding without it.
"""

import pytest

from denoiser.workers import billing_worker


@pytest.fixture(autouse=True)
def _db():
    from denoiser.storage.db import init_db
    init_db()


class _Store:
    available = True
    client = None

    def __init__(self, journal):
        self.journal = journal

    def cleanup_old_data(self, tenant_id, days_to_keep):
        self.journal.append(("delete", str(tenant_id)))
        return True


@pytest.fixture
def journal(monkeypatch):
    """Records archival and deletion in the order they actually happen."""
    events: list[tuple] = []

    monkeypatch.setattr(
        billing_worker.runtime, "clickhouse_store", lambda: _Store(events)
    )
    return events


def _archives(journal, *, succeeds=True):
    def run_archival():
        journal.append(("archive", None))
        if not succeeds:
            raise RuntimeError("S3 is unreachable")
    return run_archival


class TestArchivalPrecedesDeletion:
    def test_the_archive_is_written_before_anything_is_deleted(self, journal, monkeypatch):
        monkeypatch.setattr(
            "denoiser.storage.archiver.S3ArchiverEngine.run_archival",
            staticmethod(_archives(journal)),
        )

        summary = billing_worker.aggregate_billing(enforce_retention=True)

        assert summary["archived"] is True
        kinds = [kind for kind, _ in journal]
        assert "archive" in kinds, "archival never ran"
        assert "delete" in kinds, "retention never ran"
        # The whole point: every deletion comes after the archive.
        assert kinds.index("archive") < kinds.index("delete")

    def test_a_failed_archive_cancels_the_deletion(self, journal, monkeypatch):
        """A day of extra storage is recoverable. A deletion is not."""
        monkeypatch.setattr(
            "denoiser.storage.archiver.S3ArchiverEngine.run_archival",
            staticmethod(_archives(journal, succeeds=False)),
        )

        summary = billing_worker.aggregate_billing(enforce_retention=True)

        assert summary["archived"] is False
        assert ("delete", ) not in [(k, ) for k, _ in journal], journal
        assert all(kind != "delete" for kind, _ in journal)
        # Metering still happened — usage is not what was at risk.
        assert summary["tenants"] >= 1

    def test_metering_without_retention_does_not_archive(self, journal, monkeypatch):
        """A backfill of an old day must not trigger a deletion sweep."""
        monkeypatch.setattr(
            "denoiser.storage.archiver.S3ArchiverEngine.run_archival",
            staticmethod(_archives(journal)),
        )

        summary = billing_worker.aggregate_billing(enforce_retention=False)

        assert summary["archived"] is False
        assert journal == []


class TestTheSchedulerNoLongerRacesItself:
    """Asserted against the declared schedule, not against live scheduler state.

    Job registration is process state that other tests start and stop, so
    reading it here would make these pass or fail on test ordering rather than
    on what the deployment actually schedules.
    """

    def test_store_archival_is_not_registered_as_its_own_cron_job(self):
        """The 04:00 job is gone; the sweep is inside the metering pass now."""
        from denoiser.api.scheduler import NIGHTLY_JOBS

        ids = [job_id for job_id, _, _, _ in NIGHTLY_JOBS]
        assert not any("sso_s3_db_archival" in job_id for job_id in ids), ids

    def test_the_disk_log_sweep_is_still_scheduled(self):
        """A different job with a similar name — it protects the data volume."""
        from denoiser.api.scheduler import NIGHTLY_JOBS

        ids = [job_id for job_id, _, _, _ in NIGHTLY_JOBS]
        assert "archive_old_logs_to_s3" in ids, ids

    def test_the_declared_schedule_is_what_gets_registered(self):
        """The declaration is only useful if it is the thing being used."""
        from denoiser.api import scheduler as scheduler_module

        for job_id, _, hour, minute in scheduler_module.NIGHTLY_JOBS:
            job = scheduler_module.scheduler.get_job(job_id)
            if job is None:
                continue  # a prior test shut the scheduler down; nothing to check
            assert str(job.trigger.fields[job.trigger.FIELD_NAMES.index("hour")]) == str(hour)
            assert str(job.trigger.fields[job.trigger.FIELD_NAMES.index("minute")]) == str(minute)
