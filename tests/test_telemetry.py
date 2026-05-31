import asyncio
import json
import os

import pytest

from denoiser.detection.metrics_correlator import MetricsCorrelator
from denoiser.telemetry.ebpf_collector import EBPFCollector
from denoiser.telemetry.metrics_collector import MetricsCollector


@pytest.fixture
def temp_metrics_file(tmp_path):
    return tmp_path / "test_stream.jsonl"

def test_metrics_collector_writes_to_file(temp_metrics_file):
    async def run_test():
        collector = MetricsCollector(data_dir=str(temp_metrics_file.parent), interval_seconds=1)
        # Override stream path
        collector.stream_path = temp_metrics_file

        collector.start()
        await asyncio.sleep(1.5)
        collector.stop()

        assert temp_metrics_file.exists()
        lines = temp_metrics_file.read_text().strip().split("\n")
        assert len(lines) >= 1

        data = json.loads(lines[0])
        assert "timestamp" in data
        assert "cpu_percent" in data
        assert "memory_percent" in data
        assert "disk_iops" in data
        assert "network_drops_per_s" in data

    asyncio.run(run_test())

def test_metrics_correlator_matches_window(temp_metrics_file):
    # Setup fake stream data
    lines = [
        {"timestamp": 1000, "cpu_percent": 10, "memory_percent": 50, "disk_iops": 5, "network_drops": 0, "network_errors": 0},
        {"timestamp": 2000, "cpu_percent": 99, "memory_percent": 50, "disk_iops": 42, "network_drops": 0, "network_errors": 0},
        {"timestamp": 3000, "cpu_percent": 10, "memory_percent": 50, "disk_iops": 5, "network_drops": 0, "network_errors": 0},
    ]
    with open(temp_metrics_file, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")

    correlator = MetricsCorrelator(stream_path=str(temp_metrics_file))

    # Check within 2000ms window (anomaly at 2000, window 500)
    context = correlator.get_context_for_anomaly(2000, window_ms=500)
    assert context["status"] == "correlated"
    assert context["peak_cpu"] == 99
    assert context["peak_disk_iops"] == 42
    assert context["telemetry_points_analyzed"] == 1

def test_metrics_correlator_no_data(temp_metrics_file):
    # File doesn't exist yet
    if temp_metrics_file.exists():
        os.remove(temp_metrics_file)

    correlator = MetricsCorrelator(stream_path=str(temp_metrics_file))
    context = correlator.get_context_for_anomaly(2000)
    assert context["status"] == "no_telemetry_available"

def test_ebpf_collector_os_aware():
    collector = EBPFCollector()
    import platform
    assert collector.is_supported == (platform.system() == "Linux")

    # Should not crash when started
    collector.start()
    collector.stop()
