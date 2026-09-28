"""
Outbound destination validation (SSRF guard).

Anything that lets a user name a URL the server will then fetch is a
server-side request forgery primitive: the request originates inside the
network perimeter, so it reaches cloud metadata services, internal admin
ports and RFC1918 hosts that the user could never reach directly.

Alert destinations are exactly that shape — the operator supplies a URL and the
platform POSTs to it. This module is the single place that decides whether a
destination is acceptable, and it is applied twice: once at registration, so a
bad destination is rejected with a clear error, and again immediately before
delivery, because DNS can change between the two (a name that resolved to a
public address at registration can later resolve to 169.254.169.254).
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

from denoiser.logging import get_logger

logger = get_logger(__name__)

ALLOWED_SCHEMES = ("http", "https")


class DestinationNotAllowed(ValueError):
    """Raised when a URL resolves to a target the platform must not call."""


def _allowlisted_hosts() -> set[str]:
    """Hosts exempted from the private-range check.

    Deployments that legitimately alert an internal service (a self-hosted
    Mattermost, an on-prem webhook receiver) name it here explicitly, rather
    than the guard being switched off wholesale.
    """
    raw = os.getenv("SEMANTICOS_WEBHOOK_ALLOWED_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _require_https() -> bool:
    """HTTPS is mandatory unless explicitly relaxed for local development.

    An alert payload carries the log line that triggered it, so plaintext
    delivery leaks the very data the platform exists to protect.
    """
    raw = os.getenv("SEMANTICOS_WEBHOOK_ALLOW_HTTP", "")
    return raw.strip().lower() not in ("1", "true", "yes", "on")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address that is not a routable public destination."""
    return (
        ip.is_private          # RFC1918 / unique-local
        or ip.is_loopback      # 127.0.0.0/8, ::1
        or ip.is_link_local    # 169.254.0.0/16 — cloud metadata lives here
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str) -> list[str]:
    """Every address a host resolves to. Empty when resolution fails."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    return [info[4][0] for info in infos]


def validate_destination(url: str) -> str:
    """Return ``url`` unchanged if it is safe to call, else raise.

    Raises
    ------
    DestinationNotAllowed
        With a message suitable for returning to the operator who supplied the
        URL — it says what was rejected, never what the server found inside.
    """
    if not url or not url.strip():
        raise DestinationNotAllowed("Destination URL must not be empty")

    parts = urlsplit(url.strip())

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise DestinationNotAllowed(
            f"Unsupported scheme '{parts.scheme}'. Only http and https are allowed."
        )

    if parts.scheme.lower() == "http" and _require_https():
        raise DestinationNotAllowed(
            "Destination must use https. Alert payloads contain log content and "
            "must not be sent in plaintext. Set SEMANTICOS_WEBHOOK_ALLOW_HTTP=true "
            "to relax this for local development."
        )

    host = (parts.hostname or "").lower()
    if not host:
        raise DestinationNotAllowed("Destination URL has no host")

    if host in _allowlisted_hosts():
        return url

    # A literal IP needs no DNS round trip and must be checked directly.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if _is_blocked_ip(literal):
            raise DestinationNotAllowed(
                f"Destination {host} is a private, loopback or link-local address."
            )
        return url

    addresses = _resolve(host)
    if not addresses:
        # A name that does not resolve cannot be an SSRF target: the connection
        # attempt simply fails. Rejecting here instead would make the guard
        # depend on working DNS, which breaks air-gapped installs and CI.
        #
        # Residual risk: a host controlled by the submitter could answer
        # NXDOMAIN now and a private address at connect time. Closing that
        # fully requires pinning the connection to a validated address rather
        # than re-resolving, which is a transport-level change.
        logger.warning("Webhook destination host did not resolve", extra={"host": host})
        return url

    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            # Deliberately does not echo the resolved address: that would turn
            # the error message into an internal-network scanner.
            raise DestinationNotAllowed(
                f"Destination host '{host}' resolves to a private, loopback or "
                "link-local address."
            )

    return url


def is_allowed(url: str) -> bool:
    """Boolean form of :func:`validate_destination`, for filtering."""
    try:
        validate_destination(url)
        return True
    except DestinationNotAllowed:
        return False
