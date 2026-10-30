"""An outage must never be storable as a measurement.

Each test here reproduces a way the platform used to write down its own blindness
as data. They are cheap tests for an expensive class of bug: once a fabricated
zero is committed to a timeseries or a billing meter, nothing downstream can
distinguish it from a real one, and no later fix can recover the truth.
"""

from __future__ import annotations

import pytest

from denoiser.storage.clickhouse_store import ClickHouseStore
from denoiser.storage.errors import StoreUnavailable


@pytest.fixture(scope="module", autouse=True)
def _schema():
    """The schema is normally created by the app's lifespan; these tests touch
    the database without a TestClient, so they establish it themselves."""
    from denoiser.storage.db import init_db

    init_db()


@pytest.fixture
def unreachable_store(monkeypatch):
    """A store that constructed successfully but cannot reach ClickHouse.

    This is the realistic failure, not an exotic one: `_init_client` catches its
    own connection error and leaves `client = None`, so every deployment without
    ClickHouse — and every CI run — is in exactly this state.
    """
    monkeypatch.setattr(ClickHouseStore, "_init_client", lambda self: None)
    store = ClickHouseStore()
    store.client = None
    return store


class TestUnavailabilityIsNotAValue:
    def test_aggregate_metric_refuses_to_invent_a_zero(self, unreachable_store):
        """It returned 0.0, and the metric worker stored it once a minute."""
        with pytest.raises(StoreUnavailable):
            unreachable_store.aggregate_metric("level:ERROR", tenant_id="1")

    def test_available_reports_the_truth(self, unreachable_store):
        assert unreachable_store.available is False

    def test_retention_says_whether_it_ran(self, unreachable_store):
        """It returned None on every path, so a caller could not tell a
        completed retention pass from one that never happened — and retention
        that silently stops is a disk that silently fills."""
        assert unreachable_store.cleanup_old_data("1", 30) is False


class TestNothingPersistsAFabricatedNumber:
    def test_the_metric_worker_skips_instead_of_recording_zero(self, monkeypatch):
        """A gap in the series is the truth; a zero is a lie that cannot be undone."""
        from denoiser.storage.db import ExtractedMetric, MetricRule, SessionLocal
        from denoiser.workers import analysis_worker

        db = SessionLocal()
        try:
            rule = MetricRule(
                tenant_id=4242, name="outage-probe", query="level:ERROR",
                aggregation="count", window_seconds=60, enabled=True,
            )
            db.add(rule)
            db.commit()
            db.refresh(rule)
            rule_id = rule.id
        finally:
            db.close()

        def _unavailable(self, *a, **k):
            raise StoreUnavailable("ClickHouse", "test")

        monkeypatch.setattr(ClickHouseStore, "_init_client", lambda self: None)
        monkeypatch.setattr(ClickHouseStore, "aggregate_metric", _unavailable)

        try:
            analysis_worker.extract_metrics()

            db = SessionLocal()
            try:
                written = db.query(ExtractedMetric).filter(
                    ExtractedMetric.rule_id == rule_id
                ).count()
                assert written == 0, (
                    "an unreachable store was recorded as a real datapoint"
                )
            finally:
                db.close()
        finally:
            db = SessionLocal()
            try:
                db.query(ExtractedMetric).filter(ExtractedMetric.rule_id == rule_id).delete()
                db.query(MetricRule).filter(MetricRule.id == rule_id).delete()
                db.commit()
            finally:
                db.close()

    def test_billing_does_not_meter_a_tenant_at_zero_during_an_outage(self, monkeypatch):
        """It committed a full day of "they sent us nothing" for every customer."""
        from denoiser.storage.db import BillingMeter, SessionLocal, Tenant
        from denoiser.workers import billing_worker

        db = SessionLocal()
        try:
            tenant = db.query(Tenant).filter(Tenant.name == "outage-billing").first()
            if tenant is None:
                tenant = Tenant(name="outage-billing", tier="pro")
                db.add(tenant)
                db.commit()
                db.refresh(tenant)
            tenant_id = tenant.id
        finally:
            db.close()

        monkeypatch.setattr(ClickHouseStore, "_init_client", lambda self: None)
        monkeypatch.setattr(ClickHouseStore, "available", property(lambda self: False))

        try:
            summary = billing_worker.aggregate_billing(enforce_retention=False)
            assert summary["failed"] >= 1, "an unreachable store was reported as a clean run"

            db = SessionLocal()
            try:
                assert db.query(BillingMeter).filter(
                    BillingMeter.tenant_id == tenant_id
                ).count() == 0, "a zero-usage meter was committed during an outage"
            finally:
                db.close()
        finally:
            db = SessionLocal()
            try:
                db.query(BillingMeter).filter(BillingMeter.tenant_id == tenant_id).delete()
                db.query(Tenant).filter(Tenant.id == tenant_id).delete()
                db.commit()
            finally:
                db.close()


class TestAMonitorLooksBrokenRatherThanHealthy:
    def test_an_outage_is_an_error_not_no_data(self):
        """Before, an outage yielded 0.0, which classified as NO_DATA — so every
        monitor watching for errors looked healthy for as long as the store was
        down. Loudest possible failure is the only safe default here."""
        from denoiser.monitors.evaluator import STATUS_ERROR, evaluate_monitor
        from denoiser.storage.db import Monitor

        class Unreachable:
            def aggregate_metric(self, *a, **k):
                raise StoreUnavailable("ClickHouse", "test")

        monitor = Monitor(
            id=1, tenant_id=1, name="errors", query="level:ERROR", window_seconds=300,
        )
        result = evaluate_monitor(monitor, store=Unreachable())
        assert result.status == STATUS_ERROR
        assert result.value is None
