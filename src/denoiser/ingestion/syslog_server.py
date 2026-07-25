"""
Syslog ingestion listener (RFC 5424 and RFC 3164/BSD).

A single syslog endpoint accepts logs from thousands of source types that speak
no other protocol — firewalls, routers, load balancers, appliances, and legacy
Unix hosts — which is why it is the highest-breadth ingestion connector. Parsed
records are normalized into the same dict shape the HTTP ``/ingest`` path uses
(``timestamp`` / ``level`` / ``source`` / ``message``), so everything downstream
(ClickHouse storage, LQL, clustering) treats them identically.

Transports: UDP and TCP (RFC 6587 octet-counted *and* newline-delimited framing),
with optional TLS on the TCP listener.
"""

from __future__ import annotations

import asyncio
import re
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from denoiser.logging import get_logger

logger = get_logger(__name__)

# RFC 5424 severity (PRI % 8) mapped to the platform's five-level vocabulary,
# matching the numeric mapping used elsewhere for syslog-derived shippers.
SEVERITY_TO_LEVEL: dict[int, str] = {
    0: "FATAL", 1: "FATAL", 2: "FATAL", 3: "ERROR",
    4: "WARN", 5: "INFO", 6: "INFO", 7: "DEBUG",
}

_NIL = "-"
_PRI_RE = re.compile(r"^<(\d{1,3})>")
_RFC5424_HEAD_RE = re.compile(r"^<(\d{1,3})>(\d{1,2})\s")
_RFC3164_RE = re.compile(
    r"^<(\d{1,3})>([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s+(.*)$",
    re.DOTALL,
)
_3164_TAG_RE = re.compile(r"^([\w./-]+)(?:\[(\d+)\])?:\s*(.*)$", re.DOTALL)


def _decode_pri(pri: int) -> tuple[int, int]:
    """(facility, severity) from a PRI value."""
    return pri // 8, pri % 8


def _nil(value: str | None) -> str | None:
    return None if value in (None, _NIL, "") else value


def _iso_to_epoch_ms(ts: str) -> int | None:
    if ts in (_NIL, "", None):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _bsd_ts_to_epoch_ms(ts: str) -> int | None:
    # "Mmm dd hh:mm:ss" with no year — assume the current UTC year.
    ts = re.sub(r"\s+", " ", ts.strip())
    try:
        parsed = datetime.strptime(ts, "%b %d %H:%M:%S")
    except ValueError:
        return None
    parsed = parsed.replace(year=datetime.now(UTC).year, tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _split_sd_and_msg(tail: str) -> tuple[str | None, str]:
    """Separate RFC 5424 STRUCTURED-DATA from the message body."""
    if tail == _NIL:
        return None, ""
    if tail.startswith("- "):
        return None, tail[2:]
    if not tail.startswith("["):
        return None, tail

    i, n, depth, end = 0, len(tail), 0, None
    while i < n:
        c = tail[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                if i + 1 < n and tail[i + 1] == "[":
                    i += 1
                    continue
                end = i + 1
                break
        i += 1
    if end is None:
        return tail, ""  # unterminated SD; keep it out of the message
    return tail[:end], tail[end:].lstrip(" ")


def parse_rfc5424(line: str) -> dict[str, Any] | None:
    m = _RFC5424_HEAD_RE.match(line)
    if not m:
        return None
    pri = int(m.group(1))
    version = m.group(2)
    rest = line[m.end():]
    parts = rest.split(" ", 5)
    while len(parts) < 6:
        parts.append("")
    ts, host, app, procid, msgid, tail = parts
    sd, msg = _split_sd_and_msg(tail)
    facility, severity = _decode_pri(pri)

    record: dict[str, Any] = {
        "level": SEVERITY_TO_LEVEL.get(severity, "INFO"),
        "source": _nil(app) or _nil(host) or "syslog",
        "host": _nil(host),
        "app": _nil(app),
        "procid": _nil(procid),
        "msgid": _nil(msgid),
        "message": msg,
        "facility": facility,
        "severity": severity,
        "syslog_version": version,
    }
    epoch = _iso_to_epoch_ms(ts)
    if epoch is not None:
        record["timestamp"] = epoch
    if sd:
        record["structured_data"] = sd
    return record


def parse_rfc3164(line: str) -> dict[str, Any] | None:
    m = _RFC3164_RE.match(line)
    if not m:
        return None
    pri = int(m.group(1))
    ts, host, tail = m.group(2), m.group(3), m.group(4)
    facility, severity = _decode_pri(pri)

    app = None
    message = tail
    tag_m = _3164_TAG_RE.match(tail)
    procid = None
    if tag_m:
        app, procid, message = tag_m.group(1), tag_m.group(2), tag_m.group(3)

    record: dict[str, Any] = {
        "level": SEVERITY_TO_LEVEL.get(severity, "INFO"),
        "source": app or host or "syslog",
        "host": host,
        "app": app,
        "procid": procid,
        "message": message,
        "facility": facility,
        "severity": severity,
        "syslog_version": "3164",
    }
    epoch = _bsd_ts_to_epoch_ms(ts)
    if epoch is not None:
        record["timestamp"] = epoch
    return record


def parse_syslog(line: str) -> dict[str, Any] | None:
    """Parse one syslog line (5424 first, then 3164). Falls back to a raw record
    when the line has a PRI but matches neither grammar; returns None only when
    the line is empty."""
    line = line.strip()
    if not line:
        return None

    record = parse_rfc5424(line)
    if record is not None:
        return record
    record = parse_rfc3164(line)
    if record is not None:
        return record

    # Has a PRI but unrecognized structure — keep it rather than drop it.
    pri_m = _PRI_RE.match(line)
    if pri_m:
        pri = int(pri_m.group(1))
        facility, severity = _decode_pri(pri)
        return {
            "level": SEVERITY_TO_LEVEL.get(severity, "INFO"),
            "source": "syslog",
            "message": line[pri_m.end():],
            "facility": facility,
            "severity": severity,
            "syslog_version": "unknown",
        }
    # No PRI at all — treat the whole thing as a message.
    return {"level": "INFO", "source": "syslog", "message": line, "syslog_version": "unknown"}


# Sink: receives a batch of normalized records + tenant id. Injectable for tests.
Sink = Callable[[list[dict[str, Any]], str], None]


class SyslogIngestor:
    """Parses framed syslog input and flushes normalized records to a sink in
    batches (by size or age)."""

    def __init__(
        self,
        sink: Sink,
        tenant_id: str = "default_tenant",
        batch_size: int = 100,
        flush_interval: float = 1.0,
    ) -> None:
        self.sink = sink
        self.tenant_id = tenant_id
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._batch: list[dict[str, Any]] = []
        self._last_flush = time.monotonic()

    def feed_line(self, line: str) -> None:
        record = parse_syslog(line)
        if record is None:
            return
        self._batch.append(record)
        if len(self._batch) >= self.batch_size:
            self.flush()

    def feed(self, data: bytes | str) -> None:
        """Feed raw bytes/str that may contain one or more newline-delimited lines."""
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
        for line in text.splitlines():
            if line.strip():
                self.feed_line(line)

    def maybe_flush(self) -> None:
        if self._batch and (time.monotonic() - self._last_flush) >= self.flush_interval:
            self.flush()

    def flush(self) -> None:
        if not self._batch:
            return
        batch, self._batch = self._batch, []
        self._last_flush = time.monotonic()
        try:
            self.sink(batch, self.tenant_id)
        except Exception as e:
            logger.error(f"Syslog sink failed for {len(batch)} records: {e}")


# ── Transports ───────────────────────────────────────────────────────────────

class _SyslogUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, ingestor: SyslogIngestor) -> None:
        self.ingestor = ingestor

    def datagram_received(self, data: bytes, addr: Any) -> None:
        # Each UDP datagram is exactly one syslog message.
        self.ingestor.feed(data)


async def _handle_tcp(ingestor: SyslogIngestor, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle a TCP connection, supporting both RFC 6587 framings."""
    buffer = b""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            buffer += chunk
            buffer = _drain_tcp_buffer(ingestor, buffer)
    except Exception as e:
        logger.warning(f"Syslog TCP connection error: {e}")
    finally:
        if buffer.strip():
            ingestor.feed(buffer)
        writer.close()


def _drain_tcp_buffer(ingestor: SyslogIngestor, buffer: bytes) -> bytes:
    """Consume complete messages from a TCP buffer; return the remainder."""
    while buffer:
        # Octet-counting framing: "MSGLEN SP MSG".
        if buffer[:1].isdigit():
            sp = buffer.find(b" ")
            if sp == -1:
                break  # length prefix not complete yet
            try:
                length = int(buffer[:sp])
            except ValueError:
                length = -1
            if length >= 0:
                start = sp + 1
                if len(buffer) < start + length:
                    break  # full frame not arrived
                ingestor.feed(buffer[start:start + length])
                buffer = buffer[start + length:]
                continue
        # Non-transparent framing: newline-delimited.
        nl = buffer.find(b"\n")
        if nl == -1:
            break
        line = buffer[:nl]
        if line.strip():
            ingestor.feed(line)
        buffer = buffer[nl + 1:]
    return buffer


async def run_syslog_server(
    ingestor: SyslogIngestor,
    udp_port: int = 514,
    tcp_port: int = 514,
    host: str = "0.0.0.0",
    tls_cert: str | None = None,
    tls_key: str | None = None,
) -> None:
    """Start UDP + TCP syslog listeners and a periodic flusher. Runs forever."""
    loop = asyncio.get_running_loop()

    udp_transport, _ = await loop.create_datagram_endpoint(
        lambda: _SyslogUDPProtocol(ingestor), local_addr=(host, udp_port)
    )
    logger.info(f"Syslog UDP listener on {host}:{udp_port}")

    ssl_ctx = None
    if tls_cert and tls_key:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(tls_cert, tls_key)

    tcp_server = await asyncio.start_server(
        lambda r, w: _handle_tcp(ingestor, r, w), host, tcp_port, ssl=ssl_ctx
    )
    logger.info(f"Syslog TCP listener on {host}:{tcp_port}{' (TLS)' if ssl_ctx else ''}")

    async def _flusher() -> None:
        while True:
            await asyncio.sleep(ingestor.flush_interval)
            ingestor.maybe_flush()

    flusher = asyncio.create_task(_flusher())
    try:
        async with tcp_server:
            await tcp_server.serve_forever()
    finally:
        flusher.cancel()
        udp_transport.close()
        ingestor.flush()


def _clickhouse_sink() -> Sink:
    """Default sink: dual-write to ClickHouse, like the /ingest fallback path."""
    from denoiser.storage.clickhouse_store import ClickHouseStore

    store = ClickHouseStore()

    def sink(records: list[dict[str, Any]], tenant_id: str) -> None:
        store.insert_logs(records, tenant_id=tenant_id)

    return sink


def _resolve_tenant_id() -> str:
    import os

    override = os.getenv("SYSLOG_TENANT_ID")
    if override:
        return override
    try:
        from denoiser.storage.db import SessionLocal, Tenant
        db = SessionLocal()
        try:
            tenant = db.query(Tenant).order_by(Tenant.id).first()
            return str(tenant.id) if tenant else "default_tenant"
        finally:
            db.close()
    except Exception:
        return "default_tenant"


def main() -> None:
    import os

    ingestor = SyslogIngestor(sink=_clickhouse_sink(), tenant_id=_resolve_tenant_id())
    asyncio.run(
        run_syslog_server(
            ingestor,
            udp_port=int(os.getenv("SYSLOG_UDP_PORT", "514")),
            tcp_port=int(os.getenv("SYSLOG_TCP_PORT", "514")),
            host=os.getenv("SYSLOG_HOST", "0.0.0.0"),
            tls_cert=os.getenv("SYSLOG_TLS_CERT"),
            tls_key=os.getenv("SYSLOG_TLS_KEY"),
        )
    )


if __name__ == "__main__":
    main()
