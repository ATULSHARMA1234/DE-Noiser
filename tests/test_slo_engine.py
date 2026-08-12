from datetime import datetime
from unittest.mock import MagicMock

from denoiser import runtime
from denoiser.slo.engine import calculate_slo_status
from denoiser.storage.clickhouse_store import ClickHouseStore
from denoiser.storage.db import ServiceLevelObjective


class _Store:
    """Installs a real store over a stub client, through the one seam.

    Every test here used to `patch("denoiser.slo.engine.ClickHouseStore")` — the
    class as bound inside the engine's own module. That is a patch by module
    path: it works only while the engine keeps importing the class directly, and
    it had to be repeated ten times. The engine now asks `runtime` for the
    store, so substitution is one call and the object under test is the real
    store, not a MagicMock that would happily return a MagicMock from `scope()`.
    """

    def __init__(self, client):
        self.client = client

    def __enter__(self):
        runtime.set_clickhouse_store(ClickHouseStore(client=self.client))
        return self

    def __exit__(self, *exc):
        runtime.reset()
        return False


def store_with(client):
    return _Store(client)


class TestSLOStatusCalculation:
    """Tests the calculation logic inside the SLO status engine."""

    def test_calculate_slo_status_clickhouse_unavailable(self):
        slo = ServiceLevelObjective(
            id=1,
            tenant_id=1,
            name="Test SLO",
            service="auth-service",
            sli_type="availability",
            target_percentage=99.9,
            window_days=30
        )

        db_mock = MagicMock()
        # Mock ClickHouseStore to return client = None
        with store_with(None):
            status = calculate_slo_status(db_mock, slo)

            # An unreachable store means we know nothing about this objective.
            # Reporting HEALTHY/100% here claimed a passing SLO on no evidence.
            assert status["status"] == "NO_DATA"
            assert status["measured_events"] == 0
            assert status["error_budget_total"] == 0
            assert status["error_budget_remaining"] == 0
            assert status["data_points"] == []

    def test_calculate_slo_availability_healthy(self):
        slo = ServiceLevelObjective(
            id=2,
            tenant_id=1,
            name="Availability SLO",
            service="payment-service",
            sli_type="availability",
            target_percentage=99.0,
            window_days=30
        )

        db_mock = MagicMock()
        mock_client = MagicMock()
        with store_with(mock_client):
            
            # Setup mock queries:
            # 1st query: total events (1000)
            mock_total_result = MagicMock()
            mock_total_result.result_rows = [[1000]]
            
            # 2nd query: good events (995)
            mock_good_result = MagicMock()
            mock_good_result.result_rows = [[995]]
            
            # 3rd query: daily timeseries points
            mock_ts_result = MagicMock()
            mock_ts_result.result_rows = [
                [datetime(2026, 6, 1), 600, 597],
                [datetime(2026, 6, 2), 400, 398]
            ]
            
            mock_client.query.side_effect = [
                mock_total_result,
                mock_good_result,
                mock_ts_result
            ]

            status = calculate_slo_status(db_mock, slo)

            assert status["slo_id"] == 2
            assert status["current_value"] == 99.5
            # Allowed failures: 1000 * 0.01 = 10
            assert status["error_budget_total"] == 10
            # Actual failures: 5, remaining: 5
            assert status["error_budget_remaining"] == 5
            assert status["status"] == "HEALTHY"
            assert len(status["data_points"]) == 2
            assert status["data_points"][0]["value"] == 99.5  # 597/600 * 100

    def test_calculate_slo_availability_breached(self):
        slo = ServiceLevelObjective(
            id=3,
            tenant_id=1,
            name="Availability SLO Breached",
            service="payment-service",
            sli_type="availability",
            target_percentage=99.5,
            window_days=30
        )

        db_mock = MagicMock()
        mock_client = MagicMock()
        with store_with(mock_client):
            
            # 1st query: total events (1000)
            mock_total_result = MagicMock()
            mock_total_result.result_rows = [[1000]]
            
            # 2nd query: good events (990) -> 10 failures
            mock_good_result = MagicMock()
            mock_good_result.result_rows = [[990]]
            
            # 3rd query: daily timeseries points
            mock_ts_result = MagicMock()
            mock_ts_result.result_rows = []
            
            mock_client.query.side_effect = [
                mock_total_result,
                mock_good_result,
                mock_ts_result
            ]

            status = calculate_slo_status(db_mock, slo)

            assert status["slo_id"] == 3
            assert status["current_value"] == 99.0
            # Allowed failures: 1000 * 0.005 = 5
            assert status["error_budget_total"] == 5
            # Actual failures: 10, remaining: -5
            assert status["error_budget_remaining"] == -5
            assert status["status"] == "BREACHED"


class TestLatencySLI:
    """A latency SLO used to be arithmetically incapable of failing.

    The engine counted a log line as "good" when it had no duration field at
    all (`JSONHas(raw_json,'duration_ms') = 0`). Since the overwhelming majority
    of log lines carry no duration, every latency SLO reported 100% forever.
    The measurable subset is now the denominator, and an unmeasurable window is
    NO_DATA rather than a passing score.
    """

    def _slo(self, **overrides):
        defaults = dict(
            id=10,
            tenant_id=1,
            name="Latency SLO",
            service="checkout-api",
            sli_type="latency",
            target_percentage=99.0,
            window_days=30,
            latency_threshold_ms=500.0,
        )
        defaults.update(overrides)
        return ServiceLevelObjective(**defaults)

    def _client(self, total, measured, good, timeseries=None):
        client = MagicMock()
        total_result = MagicMock()
        total_result.result_rows = [[total]]
        measured_result = MagicMock()
        measured_result.result_rows = [[measured, good]]
        ts_result = MagicMock()
        ts_result.result_rows = timeseries or []
        client.query.side_effect = [total_result, measured_result, ts_result]
        return client

    def test_duration_less_logs_are_excluded_not_counted_as_good(self):
        # 10,000 log lines, only 200 carry a duration, and 100 of those are slow.
        with store_with(self._client(total=10_000, measured=200, good=100)):
            status = calculate_slo_status(MagicMock(), self._slo())

        # Old behaviour: 9,900/10,000 = 99.0% and permanently healthy.
        assert status["current_value"] == 50.0
        assert status["measured_events"] == 200
        assert status["total_events"] == 10_000
        assert status["status"] == "BREACHED"

    def test_no_measurable_events_is_no_data_not_perfect(self):
        with store_with(self._client(total=50_000, measured=0, good=0)):
            status = calculate_slo_status(MagicMock(), self._slo())

        assert status["status"] == "NO_DATA"
        assert status["current_value"] == 0.0
        assert status["total_events"] == 50_000
        assert status["measured_events"] == 0

    def test_healthy_latency_slo(self):
        with store_with(self._client(total=5_000, measured=1_000, good=995)):
            status = calculate_slo_status(MagicMock(), self._slo())

        assert status["current_value"] == 99.5
        assert status["status"] == "HEALTHY"
        # Budget is drawn against the measurable population, not every log line.
        assert status["error_budget_total"] == 10

    def test_threshold_comes_from_the_slo_not_a_constant(self):
        with store_with(self._client(total=10, measured=10, good=10)) as store:
            status = calculate_slo_status(MagicMock(), self._slo(latency_threshold_ms=1500.0))

        assert status["threshold_ms"] == 1500.0
        sent = store.client.query.call_args_list[1].kwargs["parameters"]
        assert sent["threshold"] == 1500.0

    def test_missing_threshold_falls_back_to_the_platform_default(self):
        with store_with(self._client(total=10, measured=10, good=10)):
            status = calculate_slo_status(MagicMock(), self._slo(latency_threshold_ms=None))

        assert status["threshold_ms"] == 500.0

    def test_days_with_nothing_measurable_are_gaps_not_perfect_days(self):
        rows = [
            [datetime(2026, 6, 1), 100, 99],
            [datetime(2026, 6, 2), 0, 0],      # no measurable requests that day
            [datetime(2026, 6, 3), 50, 25],
        ]
        with store_with(self._client(
total=1_000, measured=150, good=124, timeseries=rows
)):
            status = calculate_slo_status(MagicMock(), self._slo())

        assert [p["value"] for p in status["data_points"]] == [99.0, 50.0]

    def test_availability_still_measures_over_every_log_line(self):
        """The measurable-subset rule is specific to latency."""
        client = MagicMock()
        total_result = MagicMock()
        total_result.result_rows = [[1_000]]
        good_result = MagicMock()
        good_result.result_rows = [[990]]
        ts_result = MagicMock()
        ts_result.result_rows = []
        client.query.side_effect = [total_result, good_result, ts_result]

        with store_with(client):
            status = calculate_slo_status(
                MagicMock(), self._slo(sli_type="availability", target_percentage=99.0)
            )

        assert status["measured_events"] == 1_000
        assert status["current_value"] == 99.0
        assert status["threshold_ms"] is None


class TestSLOsAreScopedToTheirOwner:
    """An SLI is measured over its own organisation's logs and nobody else's.

    The window predicate used to be `source = {service} AND timestamp >= {start}`
    and nothing more. Two organisations that both run a service called `api` —
    which is most of them — were each scored against the other's traffic, on the
    minute schedule the platform runs this on.
    """

    def _slo(self, **overrides):
        defaults = dict(
            id=42,
            tenant_id=7,
            name="Checkout availability",
            service="api",
            sli_type="availability",
            target_percentage=99.0,
            window_days=30,
        )
        defaults.update(overrides)
        return ServiceLevelObjective(**defaults)

    def test_every_query_carries_the_tenant_predicate(self):
        client = MagicMock()
        total_result = MagicMock()
        total_result.result_rows = [[100]]
        good_result = MagicMock()
        good_result.result_rows = [[99]]
        ts_result = MagicMock()
        ts_result.result_rows = []
        client.query.side_effect = [total_result, good_result, ts_result]

        with store_with(client):
            calculate_slo_status(MagicMock(), self._slo())

        assert client.query.call_count == 3
        for call in client.query.call_args_list:
            sql = call.args[0]
            assert "tenant_id = {tenant_id:String}" in sql, (
                f"unscoped SLO query would read every organisation's logs: {sql}"
            )
            assert call.kwargs["parameters"]["tenant_id"] == "7"

    def test_an_ownerless_slo_is_not_evaluated_at_all(self):
        """No tenant means no measurement — not a measurement over everyone."""
        client = MagicMock()
        with store_with(client):
            status = calculate_slo_status(MagicMock(), self._slo(tenant_id=None))

        client.query.assert_not_called()
        assert status["status"] == "NO_DATA"
        assert status["measured_events"] == 0
