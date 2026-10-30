"""
OTLP logs decoding (protobuf + JSON), dependency-free.

Real OpenTelemetry exporters default to OTLP/HTTP with **protobuf** encoding, so
a JSON-only handler silently rejects almost every real sender. Rather than pull
in the heavy ``opentelemetry-proto`` + ``protobuf`` stack, this module decodes the
fixed OTLP Logs message directly from the protobuf wire format (the schema never
changes shape), and also accepts the JSON encoding. Both paths emit the same
normalized record dict (``timestamp``/``level``/``source``/``message``) the rest
of the pipeline consumes.

OTLP Logs schema (field numbers used here):
  ExportLogsServiceRequest: resource_logs = 1
  ResourceLogs:  resource = 1, scope_logs = 2
  Resource:      attributes = 1 (repeated KeyValue)
  ScopeLogs:     log_records = 2
  LogRecord:     time_unix_nano = 1 (fixed64), severity_number = 2 (varint),
                 severity_text = 3, body = 5 (AnyValue), attributes = 6,
                 trace_id = 9 (bytes), span_id = 10 (bytes),
                 observed_time_unix_nano = 11 (fixed64)
  KeyValue:      key = 1, value = 2 (AnyValue)
  AnyValue:      string=1, bool=2, int=3, double=4, array=5, kvlist=6, bytes=7
"""

from __future__ import annotations

import struct
from typing import Any

# ── protobuf wire-format primitives ──────────────────────────────────────────

def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


def _iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for each field in a message."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        field, wtype = key >> 3, key & 0x07
        if wtype == 0:  # varint
            val, i = _read_varint(buf, i)
        elif wtype == 1:  # 64-bit
            val, i = buf[i:i + 8], i + 8
        elif wtype == 2:  # length-delimited
            length, i = _read_varint(buf, i)
            val, i = buf[i:i + length], i + length
        elif wtype == 5:  # 32-bit
            val, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"Unsupported protobuf wire type {wtype}")
        yield field, wtype, val


def _as_signed64(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


# ── OTLP message decoders ────────────────────────────────────────────────────

def _decode_any_value(buf: bytes) -> Any:
    for field, wtype, val in _iter_fields(buf):
        if field == 1 and wtype == 2:
            return val.decode("utf-8", errors="replace")
        if field == 2 and wtype == 0:
            return bool(val)
        if field == 3 and wtype == 0:
            return _as_signed64(val)
        if field == 4 and wtype == 1:
            return struct.unpack("<d", val)[0]
        if field == 5 and wtype == 2:  # ArrayValue { values = 1 }
            return [_decode_any_value(v) for f, w, v in _iter_fields(val) if f == 1]
        if field == 6 and wtype == 2:  # KeyValueList { values = 1 }
            out: dict[str, Any] = {}
            for f, _w, v in _iter_fields(val):
                if f == 1:
                    k, vv = _decode_key_value(v)
                    out[k] = vv
            return out
        if field == 7 and wtype == 2:
            return val.hex()
    return None


def _decode_key_value(buf: bytes) -> tuple[str, Any]:
    key = ""
    value: Any = None
    for field, wtype, val in _iter_fields(buf):
        if field == 1 and wtype == 2:
            key = val.decode("utf-8", errors="replace")
        elif field == 2 and wtype == 2:
            value = _decode_any_value(val)
    return key, value


def _decode_attributes(bufs: list[bytes]) -> dict[str, Any]:
    return dict(_decode_key_value(b) for b in bufs)


def _decode_log_record(buf: bytes, service_name: str) -> dict[str, Any]:
    time_ns: int | None = None
    observed_ns: int | None = None
    severity_number = 0
    severity_text = None
    message: Any = None
    attr_bufs: list[bytes] = []
    trace_id = None
    span_id = None

    for field, wtype, val in _iter_fields(buf):
        if field == 1 and wtype == 1:
            time_ns = struct.unpack("<Q", val)[0]
        elif field == 11 and wtype == 1:
            observed_ns = struct.unpack("<Q", val)[0]
        elif field == 2 and wtype == 0:
            severity_number = val
        elif field == 3 and wtype == 2:
            severity_text = val.decode("utf-8", errors="replace")
        elif field == 5 and wtype == 2:
            message = _decode_any_value(val)
        elif field == 6 and wtype == 2:
            attr_bufs.append(val)
        elif field == 9 and wtype == 2:
            trace_id = val.hex()
        elif field == 10 and wtype == 2:
            span_id = val.hex()

    return _normalize(
        service_name=service_name,
        time_ns=time_ns if time_ns else observed_ns,
        severity_number=severity_number,
        severity_text=severity_text,
        message=message,
        attributes=_decode_attributes(attr_bufs),
        trace_id=trace_id,
        span_id=span_id,
    )


def _decode_resource_logs(buf: bytes) -> list[dict[str, Any]]:
    service_name = "unknown_service"
    scope_log_bufs: list[bytes] = []
    for field, wtype, val in _iter_fields(buf):
        if field == 1 and wtype == 2:  # Resource
            for f, w, v in _iter_fields(val):
                if f == 1 and w == 2:  # attributes
                    k, vv = _decode_key_value(v)
                    if k == "service.name" and isinstance(vv, str):
                        service_name = vv
        elif field == 2 and wtype == 2:  # ScopeLogs
            scope_log_bufs.append(val)

    records: list[dict[str, Any]] = []
    for scope_buf in scope_log_bufs:
        for f, w, v in _iter_fields(scope_buf):
            if f == 2 and w == 2:  # log_records
                records.append(_decode_log_record(v, service_name))
    return records


def decode_logs_request(data: bytes) -> list[dict[str, Any]]:
    """Decode an OTLP ExportLogsServiceRequest (protobuf) into normalized records."""
    records: list[dict[str, Any]] = []
    for field, wtype, val in _iter_fields(data):
        if field == 1 and wtype == 2:  # resource_logs
            records.extend(_decode_resource_logs(val))
    return records


# ── Severity + normalization (shared by protobuf and JSON) ───────────────────

def severity_number_to_level(n: int) -> str | None:
    """OTLP SeverityNumber (1..24) to the platform's five-level vocabulary."""
    if n <= 0:
        return None
    if n <= 8:
        return "DEBUG"   # TRACE (1-4) + DEBUG (5-8)
    if n <= 12:
        return "INFO"
    if n <= 16:
        return "WARN"
    if n <= 20:
        return "ERROR"
    return "FATAL"


_TEXT_LEVEL_ALIASES = {
    "TRACE": "DEBUG", "DEBUG": "DEBUG",
    "INFO": "INFO", "INFORMATION": "INFO", "NOTICE": "INFO",
    "WARN": "WARN", "WARNING": "WARN",
    "ERROR": "ERROR", "ERR": "ERROR",
    "FATAL": "FATAL", "CRITICAL": "FATAL", "CRIT": "FATAL",
}


def _resolve_level(severity_number: int, severity_text: str | None) -> str:
    level = severity_number_to_level(severity_number)
    if level:
        return level
    if severity_text:
        return _TEXT_LEVEL_ALIASES.get(severity_text.strip().upper(), severity_text.strip().upper())
    return "INFO"


def _normalize(
    service_name: str,
    time_ns: int | None,
    severity_number: int,
    severity_text: str | None,
    message: Any,
    attributes: dict[str, Any],
    trace_id: str | None,
    span_id: str | None,
) -> dict[str, Any]:
    if not isinstance(message, str):
        message = "" if message is None else str(message)
    record: dict[str, Any] = {
        "level": _resolve_level(severity_number, severity_text),
        "source": service_name,
        "service": service_name,
        "message": message,
        "source_protocol": "otlp",
    }
    if time_ns:
        record["timestamp"] = int(time_ns // 1_000_000)  # ns -> ms
    if attributes:
        record["attributes"] = attributes
    if trace_id:
        record["trace_id"] = trace_id
    if span_id:
        record["span_id"] = span_id
    return record


# ── JSON encoding (OTLP/HTTP with encoding=json) ─────────────────────────────

def _json_any_value(v: Any) -> Any:
    if not isinstance(v, dict):
        return v
    if "stringValue" in v:
        return v["stringValue"]
    if "boolValue" in v:
        return v["boolValue"]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "arrayValue" in v:
        return [_json_any_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _json_any_value(kv.get("value", {})) for kv in v["kvlistValue"].get("values", [])}
    return v


def decode_logs_json(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Decode an OTLP logs payload in JSON encoding into normalized records."""
    records: list[dict[str, Any]] = []
    for r_log in body.get("resourceLogs", []):
        attrs = {a["key"]: _json_any_value(a.get("value", {})) for a in r_log.get("resource", {}).get("attributes", [])}
        service_name = attrs.get("service.name", "unknown_service")
        for scope_log in r_log.get("scopeLogs", []):
            for rec in scope_log.get("logRecords", []):
                time_ns = rec.get("timeUnixNano") or rec.get("observedTimeUnixNano")
                records.append(_normalize(
                    service_name=service_name,
                    time_ns=int(time_ns) if time_ns else None,
                    severity_number=int(rec.get("severityNumber", 0) or 0),
                    severity_text=rec.get("severityText"),
                    message=_json_any_value(rec.get("body", {})),
                    attributes={a["key"]: _json_any_value(a.get("value", {})) for a in rec.get("attributes", [])},
                    trace_id=rec.get("traceId"),
                    span_id=rec.get("spanId"),
                ))
    return records
