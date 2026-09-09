"""SSRF guard.

The API takes a URL from a caller and a worker fetches it, which is the exact
shape of a server-side request forgery. Two independent checks:

  1. the host must be on the configured allowlist, and
  2. every address that host resolves to must be a public unicast address.

The second check is what stops a DNS record pointing at 169.254.169.254 or a
private range. It is re-run inside the worker, because DNS can change between
submission and execution.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})


class BlockedURL(ValueError):
    pass


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise BlockedURL(f"cannot resolve host: {host}") from exc
    return sorted({info[4][0] for info in infos})


def assert_public_address(host: str) -> None:
    for addr in _resolve(host):
        ip = ipaddress.ip_address(addr)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise BlockedURL(f"host {host} resolves to non-public address {addr}")


def assert_allowed(url: str, allowed_hosts: list[str], *, check_dns: bool = True) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise BlockedURL(f"scheme not allowed: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise BlockedURL("url has no host")
    if host.lower() not in {h.lower() for h in allowed_hosts}:
        raise BlockedURL(f"host not on the allowlist: {host}")
    if parsed.port is not None and parsed.port not in (80, 443):
        raise BlockedURL(f"port not allowed: {parsed.port}")
    if check_dns:
        assert_public_address(host)
    return url
