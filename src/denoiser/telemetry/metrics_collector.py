import asyncio
import json
import logging
import time
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

class MetricsCollector:
    """
    Background daemon that periodically collects host telemetry
    and appends it to a JSONL stream for temporal correlation.
    """

    def __init__(self, data_dir: str = "data", interval_seconds: int = 5):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True, parents=True)
        self.stream_path = self.data_dir / "metrics_stream.jsonl"
        self.interval_seconds = interval_seconds
        self._running = False
        self._task = None
        
        # Initialize net IO counters to get a baseline for diffing
        try:
            self._last_net = psutil.net_io_counters()
        except Exception:
            # Sandboxes/locked-down environments may block this syscall.
            # We'll still emit stable telemetry fields by treating deltas as 0.
            self._last_net = None
        self._last_net_time = time.time()

        # Disk IO counters can be unsupported depending on OS/config.
        # We still emit disk keys (zeroed) so correlation/UI stay stable.
        try:
            self._last_disk = psutil.disk_io_counters()
        except Exception:
            self._last_disk = None
        self._last_disk_time = time.time()

    def start(self):
        """Starts the background collection task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._collect_loop())
        logger.info(f"MetricsCollector started. Writing to {self.stream_path} every {self.interval_seconds}s")

    def stop(self):
        """Stops the collection task."""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("MetricsCollector stopped.")

    async def _collect_loop(self):
        while self._running:
            try:
                metrics = self.collect_snapshot()
                self._append_to_stream(metrics)
            except Exception as e:
                logger.error(f"Failed to collect metrics: {e}")
            
            await asyncio.sleep(self.interval_seconds)

    def collect_snapshot(self) -> dict:
        """Takes a synchronous snapshot of current host metrics."""
        now = time.time()
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        
        elapsed_net_s = max(1e-6, now - self._last_net_time)

        # Network diffs (drops/errors)
        network_drop_count = 0.0
        network_error_count = 0.0
        network_drops_per_s = 0.0
        network_errors_per_s = 0.0
        try:
            if self._last_net is not None:
                net_now = psutil.net_io_counters()
                drop_in = net_now.dropin - self._last_net.dropin
                drop_out = net_now.dropout - self._last_net.dropout
                err_in = net_now.errin - self._last_net.errin
                err_out = net_now.errout - self._last_net.errout

                drop_in = max(0, drop_in)
                drop_out = max(0, drop_out)
                err_in = max(0, err_in)
                err_out = max(0, err_out)

                network_drop_count = float(drop_in + drop_out)
                network_error_count = float(err_in + err_out)
                network_drops_per_s = network_drop_count / elapsed_net_s
                network_errors_per_s = network_error_count / elapsed_net_s

                self._last_net = net_now
            else:
                # Try setting baseline lazily.
                self._last_net = psutil.net_io_counters()
            self._last_net_time = now
        except Exception:
            # Keep network metrics at 0 on errors.
            self._last_net_time = now

        # Disk IO diffs (Task 14)
        disk_iops = 0.0
        disk_read_iops = 0.0
        disk_write_iops = 0.0
        disk_read_bytes_per_s = 0.0
        disk_write_bytes_per_s = 0.0

        try:
            if self._last_disk is not None:
                disk_now = psutil.disk_io_counters()
                elapsed_disk_s = max(1e-6, now - self._last_disk_time)

                read_count_delta = max(0, disk_now.read_count - self._last_disk.read_count)
                write_count_delta = max(0, disk_now.write_count - self._last_disk.write_count)
                read_bytes_delta = max(0, disk_now.read_bytes - self._last_disk.read_bytes)
                write_bytes_delta = max(0, disk_now.write_bytes - self._last_disk.write_bytes)

                disk_read_iops = read_count_delta / elapsed_disk_s
                disk_write_iops = write_count_delta / elapsed_disk_s
                disk_iops = (read_count_delta + write_count_delta) / elapsed_disk_s
                disk_read_bytes_per_s = read_bytes_delta / elapsed_disk_s
                disk_write_bytes_per_s = write_bytes_delta / elapsed_disk_s

                self._last_disk = disk_now
                self._last_disk_time = now
            else:
                # Try initializing disk counters lazily if they weren't available earlier.
                self._last_disk = psutil.disk_io_counters()
                self._last_disk_time = now
        except Exception:
            # Keep disk metrics at 0 on errors.
            pass

        return {
            "timestamp": int(now * 1000),
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_used_mb": memory.used / (1024 * 1024),
            # Disk IO (Task 14)
            "disk_iops": disk_iops,
            "disk_read_iops": disk_read_iops,
            "disk_write_iops": disk_write_iops,
            "disk_read_bytes_per_s": disk_read_bytes_per_s,
            "disk_write_bytes_per_s": disk_write_bytes_per_s,
            # Network drops/errors (Task 14)
            "network_drops": network_drop_count,
            "network_errors": network_error_count,
            # Convenience per-second values for UI
            "network_drops_per_s": network_drops_per_s,
            "network_errors_per_s": network_errors_per_s,
        }

    def _append_to_stream(self, metrics: dict):
        # We append to the file. In production (Phase 7), this gets rotated.
        with open(self.stream_path, "a") as f:
            f.write(json.dumps(metrics) + "\n")
