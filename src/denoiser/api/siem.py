"""Forward audit events to the customer's SIEM.

Audit records were written to a Postgres table and read back through the
platform's own UI. That satisfies nobody's security operations team: an audit
trail that lives only inside the audited system is one an attacker with
sufficient access can edit, and it is invisible to the correlation rules, the
retention policy and the on-call rota that already exist in Splunk, Sentinel or
QRadar. "Send us your audit events" is a line item on essentially every
enterprise security questionnaire.

Two wire formats, because between them they cover what SIEMs actually ingest:

* **Syslog RFC 5424** over UDP or TCP — the universal option, and what a
  syslog-ng/rsyslog collector in front of the SIEM expects.
* **CEF** (ArcSight Common Event Format) — carried inside the same syslog
  frame, and parsed natively by Splunk, Sentinel and QRadar without a custom
  extractor.

Delivery is fire-and-forget on a background task and never raises into the
request path. That is a deliberate trade with a stated consequence: the SIEM is
a *copy* of the audit trail, not the system of record, and taking the API down
because a collector is unreachable would make a logging outage into a customer
outage. The database write is what must not be lost, and it happens first.
"""

from __future__ import annotations

import os
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from denoiser.logging import get_logger

logger = get_logger(__name__)

#: Syslog facility 13 (log audit), severity 5 (notice) -> 13*8 + 5.
SYSLOG_PRIORITY = 109

CEF_VENDOR = "SemanticOS"
CEF_PRODUCT = "SemanticOS"
CEF_VERSION = "1"


@dataclass(frozen=True)
class SIEMConfig:
    enabled: bool
    host: str
    port: int
    protocol: str  # "udp" | "tcp" | "tls"
    fmt: str       # "cef" | "syslog"

    @property
    def is_stream(self) -> bool:
        return self.protocol in ("tcp", "tls")


def get_siem_config() -> SIEMConfig:
    """Read the forwarding target from the environment.

    Absent configuration means disabled, not misconfigured — a deployment with
    no SIEM is an ordinary deployment, and it must not log a warning per audit
    event about it.
    """
    host = os.getenv("SIEM_HOST", "").strip()
    return SIEMConfig(
        enabled=bool(host),
        host=host,
        port=int(os.getenv("SIEM_PORT", "514")),
        protocol=os.getenv("SIEM_PROTOCOL", "udp").strip().lower(),
        fmt=os.getenv("SIEM_FORMAT", "cef").strip().lower(),
    )


def _escape_cef_header(value: Any) -> str:
    """CEF header fields escape backslash and pipe.

    Newlines are stripped here too, not only in the extension. The header
    carries the request path (via the event name), the path comes from the URL,
    and a syslog record ends at a newline: an unescaped one terminates this
    event and lets whatever follows be parsed as a second, attacker-authored
    audit record. Forging an entry in the SIEM is a more useful outcome to an
    attacker than most of what the audit log is watching for.
    """
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _escape_cef_extension(value: Any) -> str:
    """CEF extension values escape backslash and equals, and drop newlines for
    the same reason as the header."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _severity(status_code: int) -> int:
    """CEF severity 0-10. A denied action matters more than a successful one:
    a 403 is somebody probing a boundary, and that is what a SIEM rule fires on."""
    if status_code in (401, 403):
        return 7
    if status_code >= 500:
        return 6
    if status_code >= 400:
        return 4
    return 3


def format_cef(event: dict[str, Any]) -> str:
    """One CEF record. Field names are the ArcSight dictionary's, so an
    off-the-shelf SIEM parser maps them without configuration."""
    status = int(event.get("status_code") or 0)
    header = "|".join(
        [
            f"CEF:{CEF_VERSION}",
            _escape_cef_header(CEF_VENDOR),
            _escape_cef_header(CEF_PRODUCT),
            _escape_cef_header(event.get("product_version", "1.0")),
            _escape_cef_header(event.get("action", "UNKNOWN")),
            _escape_cef_header(f"{event.get('action')} {event.get('resource_type')}"),
            str(_severity(status)),
        ]
    )

    extensions = {
        "rt": int(datetime.now(UTC).timestamp() * 1000),
        "suid": event.get("user_id"),
        "src": event.get("ip_address"),
        "request": event.get("resource_type"),
        "requestMethod": event.get("action"),
        "outcome": "success" if 200 <= status < 400 else "failure",
        "cs1Label": "tenantId",
        "cs1": event.get("tenant_id"),
        "cn1Label": "httpStatus",
        "cn1": status,
    }
    if event.get("changes"):
        extensions["cs2Label"] = "changes"
        extensions["cs2"] = event["changes"]

    body = " ".join(
        f"{k}={_escape_cef_extension(v)}"
        for k, v in extensions.items()
        if v is not None and v != ""
    )
    return f"{header}|{body}"


def format_syslog(event: dict[str, Any], message: str, hostname: str | None = None) -> str:
    """Wrap a message in an RFC 5424 frame."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    host = hostname or socket.gethostname()
    return (
        f"<{SYSLOG_PRIORITY}>1 {timestamp} {host} {CEF_PRODUCT} "
        f"{os.getpid()} audit - {message}"
    )


def render(event: dict[str, Any], config: SIEMConfig | None = None) -> str:
    """The full wire payload for one audit event."""
    config = config or get_siem_config()
    if config.fmt == "cef":
        return format_syslog(event, format_cef(event))
    return format_syslog(
        event,
        " ".join(
            f"{k}={v}"
            for k, v in event.items()
            if v is not None and v != ""
        ),
    )


def send(payload: str, config: SIEMConfig | None = None) -> bool:
    """Deliver one record. Returns whether it went out; never raises.

    A new connection per event rather than a pooled one: audit volume is
    request-rate, not log-rate, and a pooled socket to a collector that has
    silently gone away fails on the *next* event rather than this one, which
    makes the loss harder to attribute than it is worth.
    """
    config = config or get_siem_config()
    if not config.enabled:
        return False

    data = payload.encode("utf-8")
    try:
        if config.protocol == "udp":
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2)
                sock.sendto(data, (config.host, config.port))
            return True

        with socket.create_connection((config.host, config.port), timeout=2) as raw:
            if config.protocol == "tls":
                context = ssl.create_default_context()
                with context.wrap_socket(raw, server_hostname=config.host) as tls:
                    # Octet-counting framing (RFC 6587): a TCP stream has no
                    # message boundaries, and newline framing corrupts any
                    # record containing one.
                    tls.sendall(f"{len(data)} ".encode() + data)
            else:
                raw.sendall(f"{len(data)} ".encode() + data)
        return True
    except Exception as exc:
        # Logged, not raised: the database write already succeeded and is the
        # system of record. Failing the request here would turn an unreachable
        # collector into a customer-visible outage.
        logger.warning("Could not forward an audit event to the SIEM: %s", exc)
        return False


def forward(event: dict[str, Any]) -> bool:
    """Render and deliver one audit event, if forwarding is configured."""
    config = get_siem_config()
    if not config.enabled:
        return False
    return send(render(event, config), config)
