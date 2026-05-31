import json
import logging
import platform
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

class EBPFCollector:
    """
    Hooks into Linux kernel events (eBPF) using bcc to track
    TCP retransmits, OOM kills, and deep system anomalies.
    Gracefully disables itself on macOS/Windows.
    """
    def __init__(self):
        self.os_type = platform.system()
        self.is_supported = self.os_type == "Linux"
        self._running = False
        self._thread: threading.Thread | None = None

        self.events_path = Path("data") / "ebpf_events.jsonl"

        if not self.is_supported:
            logger.warning(f"eBPF tracing is not supported on {self.os_type}. eBPFCollector disabled.")

    def _handle_event_factory(self, bpf: object):
        # bcc hands us raw bytes; we parse them using the bcc-generated event type.
        def _handle_event(_cpu: int, data: bytes, _size: int):
            try:
                event = bpf["events"].event(data)
                ts_ms = int(time.time() * 1000)
                comm = getattr(event, "comm", b"")
                comm_str = comm.split(b"\x00", 1)[0].decode("utf-8", errors="replace") if isinstance(comm, (bytes, bytearray)) else str(comm)

                payload = {
                    "timestamp": ts_ms,
                    "event_type": int(getattr(event, "event_type", 0)),
                    "pid": int(getattr(event, "pid", 0)),
                    "comm": comm_str,
                }
                self.events_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.events_path, "a") as f:
                    f.write(json.dumps(payload) + "\n")
            except Exception as e:
                logger.error(f"Failed to handle eBPF event: {e}")

        return _handle_event

    def start(self):
        if not self.is_supported:
            return
        logger.info("Starting eBPF kernel tracing...")
        try:
            from bcc import BPF  # type: ignore
        except Exception as e:
            # Optional dependency: gracefully disable if not present.
            logger.warning(f"bcc is not available; eBPF tracing disabled. ({e})")
            return

        # Best-effort minimal program:
        # - TCP retransmits: kprobe tcp_retransmit_skb (may vary by kernel)
        # - OOM kills: kprobe oom_kill_process (may vary by kernel)
        #
        # DNS latency capture is intentionally left out of this lightweight phase-3 implementation.
        bpf_program = r"""
#include <uapi/linux/ptrace.h>

struct event_t {
    u64 ts_ns;
    u32 event_type;   // 1=retransmit, 2=oom_kill
    u32 pid;
    char comm[16];
};

BPF_PERF_OUTPUT(events);

int on_retransmit(struct pt_regs *ctx) {
    struct event_t ev = {};
    ev.ts_ns = bpf_ktime_get_ns();
    ev.event_type = 1;
    ev.pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&ev.comm, sizeof(ev.comm));
    events.perf_submit(ctx, &ev, sizeof(ev));
    return 0;
}

int on_oom_kill(struct pt_regs *ctx) {
    struct event_t ev = {};
    ev.ts_ns = bpf_ktime_get_ns();
    ev.event_type = 2;
    ev.pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&ev.comm, sizeof(ev.comm));
    events.perf_submit(ctx, &ev, sizeof(ev));
    return 0;
}
"""

        try:
            self._bpf = BPF(text=bpf_program)  # type: ignore[attr-defined]
            # Attach probes. If symbols are missing on a given kernel, this will throw.
            self._bpf.attach_kprobe(event="tcp_retransmit_skb", fn_name="on_retransmit")  # type: ignore[attr-defined]
            self._bpf.attach_kprobe(event="oom_kill_process", fn_name="on_oom_kill")  # type: ignore[attr-defined]

            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            self._running = True

            self._bpf["events"].open_perf_buffer(self._handle_event_factory(self._bpf))  # type: ignore[index,attr-defined]

            def _poll_loop():
                while self._running:
                    try:
                        # Poll with a small timeout so stop() can take effect quickly.
                        self._bpf.perf_buffer_poll(timeout=100)  # type: ignore[attr-defined]
                    except Exception as e:
                        logger.error(f"eBPF perf buffer poll failed: {e}")
                        time.sleep(1)

            self._thread = threading.Thread(target=_poll_loop, name="ebpf-poll", daemon=True)
            self._thread.start()
        except Exception as e:
            logger.warning(f"eBPF tracing failed to initialize; disabling. ({e})")
            self._running = False

    def stop(self):
        self._running = False
        if self.is_supported:
            logger.info("Stopping eBPF kernel tracing.")
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
