"""Turn an MCP transport failure into something a caller can act on.

A bare "unreachable: ..." 502 leaves the caller nowhere to go: the server may be
down, the URL may be wrong, or — the common case on the user tier — the
endpoint is fine but the request needs authorization. Those are three different
fixes, so a failure carries a machine-readable reason a UI can act on, plus the
OAuth protected-resource metadata URL when the server advertised one
(RFC 9728: ``WWW-Authenticate: Bearer resource_metadata="..."``). We do not
fetch that URL: the MCP SDK's ``OAuthClientProvider`` owns discovery; the
classifier only reports what the server already told us.

Used by the hub's Apps host proxy (which answers with the failure's status and
reason), by the graph-side loader (which logs the reason before dropping a
server) and by the debug probe (which shows both).
"""

import re
import ssl
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent_server.infra.circuit_breaker import CircuitOpenError

# RFC 9728's parameter, as it appears in a WWW-Authenticate challenge. This is a
# defined header grammar, not prose — a regex is the right tool.
_RESOURCE_METADATA = re.compile(r'resource_metadata\s*=\s*"([^"]+)"', re.IGNORECASE)


class McpFailureReason(StrEnum):
    """Why an MCP call or handshake failed."""

    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    TLS_ERROR = "tls_error"
    AUTH_REQUIRED = "auth_required"
    HTTP_ERROR = "http_error"
    CIRCUIT_OPEN = "circuit_open"
    INVALID_CONFIG = "invalid_config"


# What an HTTP surface should answer with. A caller cannot fix the first two,
# can fix auth_required, and should retry later on circuit_open.
_STATUS_CODES: dict[McpFailureReason, int] = {
    McpFailureReason.UNREACHABLE: 502,
    McpFailureReason.TIMEOUT: 504,
    McpFailureReason.TLS_ERROR: 502,
    McpFailureReason.AUTH_REQUIRED: 401,
    McpFailureReason.HTTP_ERROR: 502,
    McpFailureReason.CIRCUIT_OPEN: 503,
    McpFailureReason.INVALID_CONFIG: 500,
}


@dataclass(frozen=True)
class McpFailure:
    """A classified MCP failure, safe to echo to a caller."""

    server: str
    reason: McpFailureReason
    message: str
    resource_metadata: str | None = None

    @property
    def status_code(self) -> int:
        return _STATUS_CODES[self.reason]

    def as_detail(self) -> dict[str, Any]:
        """The JSON body an HTTP surface returns for this failure."""
        detail: dict[str, Any] = {
            "message": self.message,
            "reason": self.reason.value,
            "server": self.server,
        }
        if self.resource_metadata:
            detail["resource_metadata"] = self.resource_metadata
        return detail


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """The exception and its causes/contexts, each visited once."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


# TLS failures are their own fix (certificate, CA bundle, expiry), so they get
# their own reason. The markers are the specific ones: a bare "handshake" or
# "ssl" also appears in plain transport failures, and pointing an operator at a
# certificate when the server is simply down is worse than saying nothing.
_TLS_MARKERS = (
    "certificate verify failed",
    "certificate_verify_failed",
    "sslcertverificationerror",
    "self-signed certificate",
    "certificate has expired",
    "record layer failure",
    "tlsv1 alert",
)


def _looks_like_tls_failure(chain: list[BaseException]) -> bool:
    for exc in chain:
        if isinstance(exc, ssl.SSLError):
            return True
        text = str(exc).lower()
        if any(marker in text for marker in _TLS_MARKERS):
            return True
    return False


def _resource_metadata(chain: list[BaseException]) -> str | None:
    for exc in chain:
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        challenge = headers.get("WWW-Authenticate") or headers.get("www-authenticate")
        if challenge:
            match = _RESOURCE_METADATA.search(challenge)
            if match:
                return match.group(1)
    return None


def classify_mcp_error(exc: BaseException, *, server: str) -> McpFailure:
    """Classify *exc* (and whatever it wraps) for *server*.

    Our own ``asyncio.timeout`` deadline is what times out in practice, so a
    builtin ``TimeoutError`` is the timeout signal; a transport-level timeout
    that reaches us first is reported as unreachable, which is what the caller
    would conclude anyway.
    """
    chain = list(_exception_chain(exc))

    if any(isinstance(item, CircuitOpenError) for item in chain):
        return McpFailure(
            server=server,
            reason=McpFailureReason.CIRCUIT_OPEN,
            message=f"MCP server {server!r} is failing repeatedly and is temporarily skipped",
        )

    response = next((getattr(item, "response", None) for item in chain if getattr(item, "response", None)), None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status in (401, 403):
            return McpFailure(
                server=server,
                reason=McpFailureReason.AUTH_REQUIRED,
                message=f"MCP server {server!r} rejected the credentials (HTTP {status}); re-authorize the connection",
                resource_metadata=_resource_metadata(chain),
            )
        return McpFailure(
            server=server,
            reason=McpFailureReason.HTTP_ERROR,
            message=f"MCP server {server!r} answered HTTP {status}",
        )

    if any(isinstance(item, TimeoutError) for item in chain):
        return McpFailure(
            server=server,
            reason=McpFailureReason.TIMEOUT,
            message=f"MCP server {server!r} timed out",
        )

    if _looks_like_tls_failure(chain):
        return McpFailure(
            server=server,
            reason=McpFailureReason.TLS_ERROR,
            message=(
                f"MCP server {server!r} failed TLS verification: {str(exc)[:200]} — "
                "check the server certificate, or CUSTOM_CA_PATH if it uses a private CA"
            ),
        )

    # DNS or TCP: from the caller's side that server is simply not answering.
    return McpFailure(
        server=server,
        reason=McpFailureReason.UNREACHABLE,
        message=f"MCP server {server!r} unreachable: {str(exc)[:300]}",
    )
