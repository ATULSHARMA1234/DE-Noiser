import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MetricsCorrelator:
    """
    Scans the metrics_stream.jsonl to find the host state (CPU/Mem/Network)
    at the exact time a semantic log anomaly occurred, and folds in the kernel
    events (TCP retransmits, OOM kills) captured over the same window.
    """
    def __init__(self, stream_path: str = "data/metrics_stream.jsonl"):
        self.stream_path = Path(stream_path)
        self._telemetry_cache: list[dict[str, Any]] | None = None
        self._kernel_cache: list[dict[str, Any]] | None = None

    def _load_kernel_events(self) -> list[dict[str, Any]]:
        """Kernel events recorded by the eBPF collector, sorted by time.

        These were written to disk and never read by anything — an OOM kill
        landing at the same second as a burst of anomalies is exactly the kind
        of evidence this correlation exists to surface.
        """
        if self._kernel_cache is not None:
            return self._kernel_cache

        try:
            from denoiser.telemetry.ebpf_collector import read_events

            self._kernel_cache = read_events()
        except Exception as e:
            logger.error(f"Error loading kernel events for correlation: {e}")
            self._kernel_cache = []
        return self._kernel_cache

    def _kernel_context(self, lower_bound: int, upper_bound: int) -> dict[str, Any]:
        """Summarise kernel events falling inside the anomaly window."""
        summary: dict[str, Any] = {
            "tcp_retransmits": 0,
            "oom_kills": 0,
            "processes": [],
            "events_analyzed": 0,
        }
        seen: set[str] = set()

        for event in self._load_kernel_events():
            ts = event.get("timestamp", 0)
            if ts < lower_bound:
                continue
            if ts > upper_bound:
                break

            summary["events_analyzed"] += 1
            name = event.get("event_name")
            if name == "tcp_retransmit":
                summary["tcp_retransmits"] += 1
            elif name == "oom_kill":
                summary["oom_kills"] += 1

            comm = event.get("comm")
            if comm and comm not in seen:
                seen.add(comm)
                summary["processes"].append(comm)

        return summary

    def _load_cache(self) -> list[dict[str, Any]]:
        if self._telemetry_cache is not None:
            return self._telemetry_cache

        if not self.stream_path.exists():
            self._telemetry_cache = []
            return self._telemetry_cache

        cache: list[dict[str, Any]] = []
        try:
            with open(self.stream_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    ts = data.get("timestamp", 0)
                    # Keep only well-formed telemetry points.
                    if isinstance(ts, (int, float)) and ts > 0:
                        cache.append(data)
        except Exception as e:
            logger.error(f"Error loading metrics stream for correlation: {e}")
            cache = []

        cache.sort(key=lambda d: d.get("timestamp", 0))
        self._telemetry_cache = cache
        return self._telemetry_cache

    def get_context_for_anomaly(self, anomaly_timestamp_ms: int, window_ms: int = 30000) -> dict[str, Any]:
        """
        Given the time of an anomaly, returns the host metrics within ± window_ms.
        """
        cache = self._load_cache()
        kernel = self._kernel_context(
            anomaly_timestamp_ms - window_ms, anomaly_timestamp_ms + window_ms
        )
        if not cache:
            # Host telemetry may be absent while kernel tracing is running (or
            # vice versa); report whichever we actually have.
            return {
                "status": "correlated" if kernel["events_analyzed"] else "no_telemetry_available",
                "kernel_events": kernel,
            }

        context: dict[str, Any] = {
            "status": "pending",
            "window_ms": window_ms,
            "pre_anomaly_cpu": None,
            "peak_cpu": 0.0,
            "peak_memory_percent": 0.0,
            "peak_disk_iops": 0.0,
            "network_drops": 0.0,
            "network_errors": 0.0,
            "telemetry_points_analyzed": 0,
        }

        lower_bound = anomaly_timestamp_ms - window_ms
        upper_bound = anomaly_timestamp_ms + window_ms

        last_cpu_before_anomaly: float | None = None
        try:
            for data in cache:
                ts = data.get("timestamp", 0)
                if ts < lower_bound:
                    if ts < anomaly_timestamp_ms:
                        maybe_cpu = data.get("cpu_percent")
                        if isinstance(maybe_cpu, (int, float)):
                            last_cpu_before_anomaly = float(maybe_cpu)
                    continue
                if ts > upper_bound:
                    break

                context["telemetry_points_analyzed"] += 1

                cpu = data.get("cpu_percent", 0) or 0
                mem = data.get("memory_percent", 0) or 0
                disk_iops = data.get("disk_iops", 0) or 0
                net_drops = data.get("network_drops", 0) or 0
                net_errors = data.get("network_errors", 0) or 0

                cpu_f = float(cpu)
                mem_f = float(mem)
                disk_iops_f = float(disk_iops)
                net_drops_f = float(net_drops)
                net_errors_f = float(net_errors)

                if cpu_f > context["peak_cpu"]:
                    context["peak_cpu"] = cpu_f
                if mem_f > context["peak_memory_percent"]:
                    context["peak_memory_percent"] = mem_f
                if disk_iops_f > context["peak_disk_iops"]:
                    context["peak_disk_iops"] = disk_iops_f

                context["network_drops"] += net_drops_f
                context["network_errors"] += net_errors_f
        except Exception as e:
            logger.error(f"Error correlating metrics: {e}")
            return {"status": "error", "message": str(e)}

        context["pre_anomaly_cpu"] = last_cpu_before_anomaly
        context["kernel_events"] = kernel

        if context["telemetry_points_analyzed"] > 0 or kernel["events_analyzed"] > 0:
            context["status"] = "correlated"
        else:
            context["status"] = "no_data_in_window"

        return context
