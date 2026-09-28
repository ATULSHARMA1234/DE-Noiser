"""Monitor evaluation.

Monitors stored a query and thresholds, but nothing ever ran them: a monitor
could never fire, and "Status" only reported whether the row was enabled. These
cover the evaluator that now runs them.
"""

import datetime

import pytest

from denoiser.monitors.evaluator import (
    STATUS_CRITICAL,
    STATUS_ERROR,
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_WARNING,
    apply_result,
    evaluate_all,
    evaluate_monitor,
    is_muted,
)
from denoiser.storage.db import AlertLog, Monitor, SessionLocal, init_db
from denoiser.utils.time import to_epoch_ms, utcnow


class FakeStore:
    """Stands in for ClickHouse; records the window it was asked about."""

    def __init__(self, value=0.0, raises=False):
        self.value = value
        self.raises = raises
        self.calls = []

    def aggregate_metric(self, query, aggregation="count", tenant_id=None, from_ts=None, to_ts=None):
        self.calls.append({"query": query, "tenant_id": tenant_id, "from_ts": from_ts, "to_ts": to_ts})
        if self.raises:
            raise RuntimeError("clickhouse unavailable")
        return self.value


def _monitor(**overrides) -> Monitor:
    defaults = dict(
        id=1, tenant_id=1, name="Payment errors", type="log alert",
        query="level:ERROR", severity="warning", enabled=True,
        threshold_warning=5.0, threshold_critical=40.0, window_seconds=300,
        status="PENDING", muted_until=None,
    )
    defaults.update(overrides)
    return Monitor(**defaults)


class TestClassification:
    @pytest.mark.parametrize(
        "value,expected",
        [(0, STATUS_NO_DATA), (4, STATUS_OK), (5, STATUS_WARNING), (39, STATUS_WARNING),
         (40, STATUS_CRITICAL), (900, STATUS_CRITICAL)],
    )
    def test_thresholds_are_inclusive_lower_bounds(self, value, expected):
        result = evaluate_monitor(_monitor(), store=FakeStore(value))
        assert result.status == expected
        assert result.value == value

    def test_no_thresholds_alerts_on_any_match(self):
        """"Tell me when this query matches" is the only sensible reading."""
        m = _monitor(threshold_warning=None, threshold_critical=None)
        assert evaluate_monitor(m, store=FakeStore(1)).status == STATUS_CRITICAL
        assert evaluate_monitor(m, store=FakeStore(0)).status == STATUS_NO_DATA

    def test_warning_only_monitor_never_reports_critical(self):
        m = _monitor(threshold_critical=None, threshold_warning=2.0)
        assert evaluate_monitor(m, store=FakeStore(1000)).status == STATUS_WARNING

    def test_query_failure_is_reported_not_swallowed(self):
        result = evaluate_monitor(_monitor(), store=FakeStore(raises=True))
        assert result.status == STATUS_ERROR
        assert result.value is None
        assert "clickhouse unavailable" in result.error
        assert not result.is_breaching


class TestWindow:
    def test_window_is_the_monitors_own_window(self):
        store = FakeStore(1)
        evaluate_monitor(_monitor(window_seconds=900), store=store)
        call = store.calls[0]
        assert call["to_ts"] - call["from_ts"] == 900 * 1000

    def test_window_ends_at_the_real_current_instant(self):
        """utcnow() is naive; .timestamp() would read it as local time.

        East of UTC that moved the window hours into the past and excluded the
        rows that had just been written.
        """
        store = FakeStore(1)
        now = utcnow()
        evaluate_monitor(_monitor(), store=store, now=now)
        true_now_ms = to_epoch_ms(now)
        assert abs(store.calls[0]["to_ts"] - true_now_ms) < 1000

    def test_query_and_tenant_are_passed_through(self):
        store = FakeStore(1)
        evaluate_monitor(_monitor(query="service:payment AND level:ERROR", tenant_id=7), store=store)
        assert store.calls[0]["query"] == "service:payment AND level:ERROR"
        assert store.calls[0]["tenant_id"] == 7


class TestMuting:
    def test_future_mute_is_muted(self):
        m = _monitor(muted_until=utcnow() + datetime.timedelta(minutes=30))
        assert is_muted(m) is True

    def test_expired_mute_is_not_muted(self):
        m = _monitor(muted_until=utcnow() - datetime.timedelta(minutes=1))
        assert is_muted(m) is False


class TestAlerting:
    @pytest.fixture(scope="class", autouse=True)
    def _db(self):
        init_db()

    @pytest.fixture
    def db(self):
        session = SessionLocal()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    def test_entering_breach_writes_an_alert(self, db):
        m = _monitor(status=STATUS_OK)
        result = evaluate_monitor(m, store=FakeStore(50))
        assert apply_result(db, m, result) is True
        assert m.status == STATUS_CRITICAL
        assert m.last_triggered_at is not None

    def test_staying_in_breach_does_not_realert(self, db):
        """Otherwise one broken service produces an alert every minute."""
        m = _monitor(status=STATUS_CRITICAL)
        result = evaluate_monitor(m, store=FakeStore(50))
        assert apply_result(db, m, result) is False

    def test_recovering_then_breaching_again_alerts_again(self, db):
        m = _monitor(status=STATUS_CRITICAL)
        apply_result(db, m, evaluate_monitor(m, store=FakeStore(1)))
        assert m.status == STATUS_OK
        assert apply_result(db, m, evaluate_monitor(m, store=FakeStore(50))) is True

    def test_muted_monitor_is_evaluated_but_does_not_alert(self, db):
        m = _monitor(status=STATUS_OK, muted_until=utcnow() + datetime.timedelta(hours=1))
        result = evaluate_monitor(m, store=FakeStore(50))
        assert apply_result(db, m, result) is False
        assert m.status == STATUS_CRITICAL, "status must stay visible while muted"
        assert m.last_triggered_at is not None

    def test_alert_row_names_the_monitor(self, db):
        m = _monitor(id=4242, status=STATUS_OK, name="Checkout 5xx")
        apply_result(db, m, evaluate_monitor(m, store=FakeStore(99)))
        db.flush()
        alert = db.query(AlertLog).filter(AlertLog.webhook_id == "monitor_engine").order_by(AlertLog.id.desc()).first()
        assert alert is not None
        assert "Checkout 5xx" in alert.error
        assert alert.priority == "critical"

    def test_evaluation_state_is_recorded_even_when_healthy(self, db):
        m = _monitor(status="PENDING")
        apply_result(db, m, evaluate_monitor(m, store=FakeStore(2)))
        assert m.status == STATUS_OK
        assert m.last_value == 2
        assert m.last_evaluated_at is not None
        assert m.last_triggered_at is None

    def test_evaluate_all_skips_disabled_monitors(self, db):
        disabled = _monitor(id=None, name="disabled-monitor-fixture", enabled=False)
        db.add(disabled)
        db.commit()
        try:
            results = evaluate_all(db, store=FakeStore(1))
            assert all(r.monitor_id is not None for r in results)
            names = {db.get(Monitor, r.monitor_id).name for r in results}
            assert "disabled-monitor-fixture" not in names
        finally:
            # The suite shares the developer's database; leaving rows behind puts
            # a fake monitor on the real Monitors page.
            db.delete(disabled)
            db.commit()
