import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from denoiser.storage.db import ServiceLevelObjective
from denoiser.slo.engine import calculate_slo_status


class TestSLOStatusCalculation:
    """Tests the calculation logic inside the SLO status engine."""

    def test_calculate_slo_status_clickhouse_unavailable(self):
        slo = ServiceLevelObjective(
            id=1,
            name="Test SLO",
            service="auth-service",
            sli_type="availability",
            target_percentage=99.9,
            window_days=30
        )

        db_mock = MagicMock()
        # Mock ClickHouseStore to return client = None
        with patch("denoiser.slo.engine.ClickHouseStore") as mock_ch_store:
            mock_ch_store.return_value.client = None
            status = calculate_slo_status(db_mock, slo)

            assert status["current_value"] == 100.0
            assert status["status"] == "HEALTHY"
            assert status["error_budget_total"] == 0
            assert status["error_budget_remaining"] == 0
            assert status["data_points"] == []

    def test_calculate_slo_availability_healthy(self):
        slo = ServiceLevelObjective(
            id=2,
            name="Availability SLO",
            service="payment-service",
            sli_type="availability",
            target_percentage=99.0,
            window_days=30
        )

        db_mock = MagicMock()
        with patch("denoiser.slo.engine.ClickHouseStore") as mock_ch_store:
            mock_client = MagicMock()
            
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
            mock_ch_store.return_value.client = mock_client

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
            name="Availability SLO Breached",
            service="payment-service",
            sli_type="availability",
            target_percentage=99.5,
            window_days=30
        )

        db_mock = MagicMock()
        with patch("denoiser.slo.engine.ClickHouseStore") as mock_ch_store:
            mock_client = MagicMock()
            
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
            mock_ch_store.return_value.client = mock_client

            status = calculate_slo_status(db_mock, slo)

            assert status["slo_id"] == 3
            assert status["current_value"] == 99.0
            # Allowed failures: 1000 * 0.005 = 5
            assert status["error_budget_total"] == 5
            # Actual failures: 10, remaining: -5
            assert status["error_budget_remaining"] == -5
            assert status["status"] == "BREACHED"
