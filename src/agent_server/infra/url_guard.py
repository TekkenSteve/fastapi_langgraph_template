"""Outbound-URL validation for user-supplied fetch targets (SSRF guard).

A user-provided URL handed to a server-side fetch is SSRF by construction:
without a gate the caller can aim the server at cloud metadata endpoints,
loopback services or private address space and read the answer back. This
module is that gate — every outbound fetch of user input goes through
``validate_public_http_url`` first:

- the scheme must be http or https;
- the host — a literal IP, or *every* address DNS answers with — must be a
  public unicast address: loopback, RFC1918, CGNAT, link-local (including the
  169.254.169.254 cloud-metadata address), unique-local, multicast, reserved
  and unspecified ranges are all refused;
- redirects are not followed here: the *caller* must re-run this validation on
  every hop, because a public URL can 302 into link-local space and a guard
  that only saw the first URL protects nothing.

Residual risk, accepted deliberately: DNS rebinding (a name that resolves
public for this validation and private for the actual fetch) is not
prevented — closing it needs resolver pinning inside the HTTP client, which
httpx does not expose. The exposure window is a single request, the fetch is
size-capped and status-echoed only, and the caller is an authenticated user;
see ``skill_fetcher`` for the fetch-side half of this contract.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Carrier-grade NAT (RFC 6598). Python only folds it into is_private from
# 3.13 on, so it is listed explicitly to keep 3.12 honest.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class UrlGuardError(ValueError):
    """The URL is not a fetchable public http(s) target."""


def _address_is_public(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for public unicast addresses; everything else is refused."""
    if addr.version == 6 and addr.ipv4_mapped is not None:
        # ::ffff:10.0.0.1 must be judged as the address it carries.
        return _address_is_public(addr.ipv4_mapped)
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False
    return not (addr.version == 4 and addr in _CGNAT)


def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Blocking DNS lookup used via run_in_executor; raises on resolution failure."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [ipaddress.ip_address(info[4][0]) for info in infos]


async def validate_public_http_url(url: str) -> str:
    """Validate a URL the server is about to fetch; returns it unchanged.

    Raises ``UrlGuardError`` with a reason that is safe to echo back to the
    caller in a 422 detail.
    """
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise UrlGuardError(f"URL scheme must be http or https, got {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise UrlGuardError("URL has no host")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _address_is_public(literal):
            raise UrlGuardError(f"refusing to fetch private or reserved address {host}")
        return url

    loop = asyncio.get_running_loop()
    try:
        answers = await loop.run_in_executor(None, _resolve, host)
    except OSError as e:
        raise UrlGuardError(f"cannot resolve host {host}: {e}") from e
    # Every answer must be public: a name mixing public and private records is
    # exactly the bypass a single-record check would invite.
    for addr in answers:
        if not _address_is_public(addr):
            raise UrlGuardError(f"host {host} resolves to private or reserved address {addr}; refusing to fetch")
    return url
