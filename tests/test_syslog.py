"""Syslog ingestion: RFC 5424 / 3164 parsing, TCP framing, UDP end-to-end, and
proof that parsed records flow through the same pipeline resolvers as /ingest."""

import asyncio

from denoiser.ingestion.syslog_server import (
    SyslogIngestor,
    _drain_tcp_buffer,
    _SyslogUDPProtocol,
    parse_syslog,
)
from denoiser.storage.clickhouse_store import (
    resolve_level,
    resolve_source,
    resolve_timestamp,
)


class TestRFC5424:
    def test_basic(self):
        line = "<34>1 2003-10-11T22:14:15.003Z mymachine.example.com su - ID47 - 'su root' failed for lonvick"
        r = parse_syslog(line)
        assert r["syslog_version"] == "1"
        assert r["facility"] == 4 and r["severity"] == 2  # 34 = 4*8 + 2
        assert r["level"] == "FATAL"  # severity 2 (critical)
        assert r["source"] == "su"
        assert r["host"] == "mymachine.example.com"
        assert r["message"] == "'su root' failed for lonvick"
        assert "timestamp" in r

    def test_structured_data_is_separated_from_message(self):
        line = ('<165>1 2003-10-11T22:14:15.003Z mymachine evntslog - ID47 '
                '[exampleSDID@32473 iut="3" eventSource="App"] An application event occurred')
        r = parse_syslog(line)
        assert r["severity"] == 5 and r["level"] == "INFO"
        assert r["structured_data"].startswith("[exampleSDID@32473")
        assert r["message"] == "An application event occurred"

    def test_nil_values(self):
        r = parse_syslog("<13>1 2024-01-01T00:00:00Z - - - - - just a message")
        assert r["host"] is None and r["app"] is None
        assert r["source"] == "syslog"  # falls back when app and host are NIL
        assert r["message"] == "just a message"


class TestRFC3164:
    def test_basic(self):
        r = parse_syslog("<34>Oct 11 22:14:15 mymachine su: 'su root' failed for lonvick")
        assert r["syslog_version"] == "3164"
        assert r["level"] == "FATAL"
        assert r["host"] == "mymachine"
        assert r["app"] == "su"
        assert r["message"] == "'su root' failed for lonvick"

    def test_with_pid(self):
        r = parse_syslog("<13>Aug  6 11:22:33 web01 nginx[1234]: 404 not found /x")
        assert r["severity"] == 5 and r["level"] == "INFO"
        assert r["app"] == "nginx"
        assert r["procid"] == "1234"
        assert r["message"] == "404 not found /x"


class TestFallbacks:
    def test_no_pri_is_kept_as_message(self):
        r = parse_syslog("a plain unstructured log line")
        assert r["level"] == "INFO"
        assert r["message"] == "a plain unstructured log line"

    def test_empty_is_none(self):
        assert parse_syslog("   ") is None


class TestPipelineCompatibility:
    """A parsed syslog record must be understood by the same resolvers the HTTP
    ingest path and ClickHouse writer use — otherwise it wouldn't cluster/query."""

    def test_resolvers_agree(self):
        r = parse_syslog("<34>1 2003-10-11T22:14:15.003Z host su - - - hello world")
        assert resolve_source(r) == "su"
        assert resolve_level(r) == "FATAL"
        assert resolve_timestamp(r).year == 2003


class TestTCPFraming:
    def test_octet_counting(self):
        got: list[dict] = []
        ing = SyslogIngestor(sink=lambda recs, t: got.extend(recs), batch_size=1)
        msg = b"<34>1 2003-10-11T22:14:15Z host app - - - hello"
        frame = str(len(msg)).encode() + b" " + msg
        assert _drain_tcp_buffer(ing, frame) == b""
        assert got[0]["message"] == "hello"

    def test_newline_framing_multiple(self):
        got: list[dict] = []
        ing = SyslogIngestor(sink=lambda recs, t: got.extend(recs), batch_size=1)
        data = b"<34>Oct 11 22:14:15 host su: one\n<13>Oct 11 22:14:16 host nginx: two\n"
        assert _drain_tcp_buffer(ing, data) == b""
        assert [g["message"] for g in got] == ["one", "two"]

    def test_partial_frame_is_retained(self):
        got: list[dict] = []
        ing = SyslogIngestor(sink=lambda recs, t: got.extend(recs), batch_size=1)
        remainder = _drain_tcp_buffer(ing, b"<34>Oct 11 22:14:15 host su: partial")
        assert remainder == b"<34>Oct 11 22:14:15 host su: partial"
        assert got == []


class TestBatching:
    def test_flushes_by_size(self):
        flushed: list[list[dict]] = []
        ing = SyslogIngestor(sink=lambda recs, t: flushed.append(recs), batch_size=2)
        ing.feed_line("<13>Oct 11 22:14:15 h a: 1")
        assert flushed == []  # not yet
        ing.feed_line("<13>Oct 11 22:14:16 h a: 2")
        assert len(flushed) == 1 and len(flushed[0]) == 2

    def test_tenant_id_is_passed_to_sink(self):
        seen = {}
        ing = SyslogIngestor(sink=lambda recs, t: seen.update({"tenant": t}), tenant_id="42", batch_size=1)
        ing.feed_line("<13>Oct 11 22:14:15 h a: x")
        assert seen["tenant"] == "42"


def test_udp_end_to_end():
    """Full path over a real UDP socket: datagram -> protocol -> ingestor -> sink."""
    async def scenario():
        received: list[dict] = []
        ingestor = SyslogIngestor(sink=lambda recs, t: received.extend(recs), batch_size=1)
        loop = asyncio.get_running_loop()

        server_transport, _ = await loop.create_datagram_endpoint(
            lambda: _SyslogUDPProtocol(ingestor), local_addr=("127.0.0.1", 0)
        )
        port = server_transport.get_extra_info("sockname")[1]

        client_transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=("127.0.0.1", port)
        )
        client_transport.sendto(b"<34>1 2003-10-11T22:14:15.003Z host su - - - via udp")
        await asyncio.sleep(0.1)
        client_transport.close()
        server_transport.close()
        return received

    received = asyncio.run(scenario())
    assert len(received) == 1
    assert received[0]["source"] == "su"
    assert received[0]["message"] == "via udp"


if __name__ == "__main__":
    test_udp_end_to_end()
