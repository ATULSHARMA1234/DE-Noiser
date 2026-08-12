"""OTLP logs ingestion: protobuf decoding (the OTel default encoding), JSON
decoding, severity mapping, and the /v1/logs endpoint accepting both — proving
records flow through the same pipeline resolvers."""

import struct

import pytest
from fastapi.testclient import TestClient

from denoiser.api.otlp_logs import (
    decode_logs_json,
    decode_logs_request,
    severity_number_to_level,
)
from denoiser.storage.clickhouse_store import resolve_level, resolve_source, resolve_timestamp

# ── Minimal OTLP protobuf encoder (test-only) ────────────────────────────────

def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | 0x80]) if n else bytes([b])
        if not n:
            return out


def _tag(field: int, wt: int) -> bytes:
    return _varint((field << 3) | wt)


def _ld(field: int, data: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(data)) + data


def _str_f(field: int, s: str) -> bytes:
    return _ld(field, s.encode())


def _vint_f(field: int, n: int) -> bytes:
    return _tag(field, 0) + _varint(n)


def _fixed64_f(field: int, n: int) -> bytes:
    return _tag(field, 1) + struct.pack("<Q", n)


def _any_string(s: str) -> bytes:
    return _str_f(1, s)  # AnyValue.string_value = 1


def _key_value(k: str, any_bytes: bytes) -> bytes:
    return _str_f(1, k) + _ld(2, any_bytes)  # KeyValue { key=1, value=2 }


def _resource(service: str) -> bytes:
    return _ld(1, _key_value("service.name", _any_string(service)))  # Resource.attributes=1


def _log_record(msg: str, severity_number: int, time_ns: int, attrs: dict | None = None) -> bytes:
    rec = _fixed64_f(1, time_ns) + _vint_f(2, severity_number) + _ld(5, _any_string(msg))
    for k, v in (attrs or {}).items():
        rec += _ld(6, _key_value(k, _any_string(v)))
    return rec


def _scope_logs(record_bufs: list[bytes]) -> bytes:
    return b"".join(_ld(2, r) for r in record_bufs)  # ScopeLogs.log_records=2


def _resource_logs(service: str, record_bufs: list[bytes]) -> bytes:
    return _ld(1, _resource(service)) + _ld(2, _scope_logs(record_bufs))


def export_logs_request(service: str, record_bufs: list[bytes]) -> bytes:
    return _ld(1, _resource_logs(service, record_bufs))  # ExportLogsServiceRequest.resource_logs=1


# ── Tests ────────────────────────────────────────────────────────────────────

class TestSeverityMapping:
    @pytest.mark.parametrize("num,level", [
        (1, "DEBUG"), (5, "DEBUG"), (9, "INFO"), (13, "WARN"),
        (17, "ERROR"), (21, "FATAL"), (24, "FATAL"), (0, None),
    ])
    def test_maps_severity_numbers(self, num, level):
        assert severity_number_to_level(num) == level


class TestProtobufDecode:
    def test_decodes_a_full_request(self):
        data = export_logs_request("payment-api", [
            _log_record("db connection timeout", 17, 1_699_999_999_000_000_000, {"http.method": "GET"}),
            _log_record("request served", 9, 1_699_999_999_500_000_000),
        ])
        records = decode_logs_request(data)
        assert len(records) == 2

        err = records[0]
        assert err["source"] == "payment-api"
        assert err["level"] == "ERROR"           # severity 17
        assert err["message"] == "db connection timeout"
        assert err["timestamp"] == 1_699_999_999_000  # ns -> ms
        assert err["attributes"]["http.method"] == "GET"

        assert records[1]["level"] == "INFO"     # severity 9

    def test_pipeline_resolvers_agree(self):
        data = export_logs_request("svc", [_log_record("boom", 21, 1_600_000_000_000_000_000)])
        r = decode_logs_request(data)[0]
        assert resolve_source(r) == "svc"
        assert resolve_level(r) == "FATAL"
        assert resolve_timestamp(r).year == 2020


class TestJsonDecode:
    def test_decodes_json_encoding(self):
        body = {"resourceLogs": [{
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "web"}}]},
            "scopeLogs": [{"logRecords": [{
                "timeUnixNano": "1699999999000000000",
                "severityNumber": 13,
                "severityText": "WARN",
                "body": {"stringValue": "slow response"},
                "attributes": [{"key": "code", "value": {"intValue": "504"}}],
            }]}],
        }]}
        records = decode_logs_json(body)
        assert len(records) == 1
        assert records[0]["source"] == "web"
        assert records[0]["level"] == "WARN"
        assert records[0]["message"] == "slow response"
        assert records[0]["attributes"]["code"] == 504


class TestEndpoint:
    @pytest.fixture(scope="class", autouse=True)
    def _db(self):
        from denoiser.storage.db import init_db
        init_db()

    @pytest.fixture
    def client(self):
        from denoiser.api.main import app
        return TestClient(app)

    def _auth(self):
        return {"X-API-Key": "semanticos-ingest-key-123"}

    def test_accepts_protobuf(self, client):
        data = export_logs_request("orders", [_log_record("oom killed", 21, 1_699_999_999_000_000_000)])
        res = client.post(
            "/v1/logs",
            content=data,
            headers={**self._auth(), "Content-Type": "application/x-protobuf"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["ingested"] == 1

    def test_accepts_json(self, client):
        body = {"resourceLogs": [{
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "web"}}]},
            "scopeLogs": [{"logRecords": [{"severityNumber": 9, "body": {"stringValue": "hi"}}]}],
        }]}
        res = client.post("/v1/logs", json=body, headers=self._auth())
        assert res.status_code == 200, res.text
        assert res.json()["ingested"] == 1

    def test_rejects_garbage(self, client):
        res = client.post(
            "/v1/logs",
            content=b"\xff\xfe not protobuf",
            headers={**self._auth(), "Content-Type": "application/x-protobuf"},
        )
        assert res.status_code == 400
