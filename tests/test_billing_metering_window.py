"""Metering has to read the day it is billing for, and survive a large one.

Two failures lived here, both silent and both in the direction of lost revenue.

The pass was scheduled at 00:00 UTC and asked for
``toDate(timestamp) = toDate(now())``. At the instant it ran, ``now()`` was the
*new* day, so the window contained the few seconds since midnight and the day
that had just closed was never read. Meters were written, on schedule, at
approximately zero, and nothing errored.

The counters were also 32-bit. A tenant sending more than about 2 GiB in a day
raised on write, and the commit that failed covered the whole pass — so one
large customer discarded every other customer's meter for that day too.
"""

from datetime import timedelta

import pytest

from denoiser.storage.db import BillingMeter, SessionLocal, Span, Tenant
from denoiser.utils.time import utcnow
from denoiser.workers.billing_worker import aggregate_billing, backfill


class _Store:
    """ClickHouse stands in as available-but-empty; the trace half is Postgres."""

    available = True
    client = None

    def cleanup_old_data(self, *a, **k):
        return True


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(
        "denoiser.workers.billing_worker.runtime.clickhouse_store", lambda: s
    )
    return s


@pytest.fixture
def tenant():
    db = SessionLocal()
    try:
        from denoiser.storage.db import init_db
        init_db()
        return db.query(Tenant).order_by(Tenant.id).first().id
    finally:
        db.close()


def _span(tenant_id, when, n):
    return Span(
        tenant_id=tenant_id,
        trace_id=f"window-trace-{n}-{when.date()}",
        span_id=f"window-span-{n}-{when.date()}",
        service_name="window-fixture",
        operation_name="work",
        start_time=when,
        end_time=when,
        duration_ms=1.0,
    )


class TestTheWindowIsTheClosedDay:
    def test_yesterdays_traffic_is_what_gets_metered(self, tenant, store):
        """The regression test for the empty window.

        Under the old behaviour this meter came back at zero no matter how much
        the customer had sent, because the query asked about the day that had
        just started.
        """
        yesterday = (utcnow() - timedelta(days=1)).replace(hour=13)
        db = SessionLocal()
        try:
            for n in range(4):
                db.add(_span(tenant, yesterday, n))
            db.commit()

            summary = aggregate_billing(db=db, enforce_retention=False)
        finally:
            db.close()

        assert summary["day"] == yesterday.date().isoformat()
        assert summary["traces"] >= 4, summary

    def test_todays_traffic_is_not_billed_yet(self, tenant, store):
        """A day still in progress is not a day you can invoice."""
        today = utcnow().replace(hour=1)
        db = SessionLocal()
        try:
            for n in range(50, 53):
                db.add(_span(tenant, today, n))
            db.commit()

            summary = aggregate_billing(db=db, enforce_retention=False)
            todays_traces = [
                s for s in db.query(Span).filter(Span.trace_id.like("window-trace-5%")).all()
            ]
            assert todays_traces, "fixture did not persist"
        finally:
            db.close()

        # Metering yesterday must not have swept today's rows in.
        assert summary["day"] != today.date().isoformat()

    def test_a_named_day_can_be_re_metered(self, tenant, store):
        """A missed run has to be recoverable, which needs an addressable day."""
        target = (utcnow() - timedelta(days=3)).replace(hour=9)
        db = SessionLocal()
        try:
            for n in range(100, 102):
                db.add(_span(tenant, target, n))
            db.commit()

            first = aggregate_billing(db=db, day=target.date(), enforce_retention=False)
            second = aggregate_billing(db=db, day=target.date(), enforce_retention=False)
        finally:
            db.close()

        assert first["traces"] >= 2
        # Re-running is an update, not a second row: the same day metered twice
        # must not become two rows that later get summed.
        assert second["traces"] == first["traces"]

        db = SessionLocal()
        try:
            rows = db.query(BillingMeter).filter(
                BillingMeter.tenant_id == tenant,
                BillingMeter.date == target.replace(hour=0, minute=0, second=0, microsecond=0),
            ).count()
        finally:
            db.close()
        assert rows == 1

    def test_backfill_covers_an_inclusive_range(self, tenant, store):
        start = (utcnow() - timedelta(days=5)).date()
        end = (utcnow() - timedelta(days=3)).date()

        results = backfill(start, end)

        assert [r["day"] for r in results] == [
            start.isoformat(),
            (start + timedelta(days=1)).isoformat(),
            end.isoformat(),
        ]

    def test_backfill_refuses_a_reversed_range(self):
        today = utcnow().date()
        with pytest.raises(ValueError):
            backfill(today, today - timedelta(days=1))


class TestALargeCustomerDoesNotBreakTheRun:
    def test_a_counter_beyond_thirty_two_bits_is_stored(self, tenant):
        """2,147,483,647 is a little over 2 GiB. That is a small day here."""
        beyond_int32 = 2_147_483_647 * 40  # ~80 GiB in a day
        when = (utcnow() - timedelta(days=9)).replace(hour=0, minute=0, second=0, microsecond=0)

        db = SessionLocal()
        try:
            db.add(BillingMeter(
                tenant_id=tenant,
                date=when,
                total_logs_ingested=beyond_int32,
                total_bytes_ingested=beyond_int32,
                total_traces_ingested=beyond_int32,
            ))
            db.commit()

            stored = db.query(BillingMeter).filter(
                BillingMeter.tenant_id == tenant, BillingMeter.date == when
            ).first()
            assert stored.total_bytes_ingested == beyond_int32
        finally:
            db.close()

    def test_one_failing_tenant_does_not_discard_the_others(self, store, monkeypatch):
        """The commit used to sit outside the per-tenant loop.

        A tenant whose write raised took every other tenant's meter down with
        it, because the rollback covered the whole pass.
        """
        from denoiser.storage.db import init_db
        init_db()

        db = SessionLocal()
        try:
            good = db.query(Tenant).order_by(Tenant.id).first()
            doomed = Tenant(name="metering-explodes-here")
            db.add(doomed)
            db.commit()
            db.refresh(doomed)
            doomed_id = doomed.id

            when = (utcnow() - timedelta(days=11)).replace(hour=7)
            db.add(_span(good.id, when, 900))
            db.commit()

            real_cleanup = store.cleanup_old_data

            def explode_for_one(tenant_id, *a, **k):
                if str(tenant_id) == str(doomed_id):
                    raise RuntimeError("simulated per-tenant failure")
                return real_cleanup(tenant_id, *a, **k)

            monkeypatch.setattr(store, "cleanup_old_data", explode_for_one)

            summary = aggregate_billing(db=db, day=when.date(), enforce_retention=True)

            assert summary["failed"] >= 1
            assert summary["metered"] >= 1

            # The healthy tenant's row survived the other one's failure.
            survived = db.query(BillingMeter).filter(
                BillingMeter.tenant_id == good.id,
                BillingMeter.date == when.replace(hour=0, minute=0, second=0, microsecond=0),
            ).first()
            assert survived is not None
        finally:
            db.close()
