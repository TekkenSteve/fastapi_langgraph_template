"""Unit tests for MCP failure classification."""

from typing import Any

import pytest

from agent_server.infra.circuit_breaker import CircuitOpenError
from agent_server.repo.graphs.mcp_errors import (
    McpFailureReason,
    classify_mcp_error,
)

_METADATA_URL = "https://auth.example.com/.well-known/oauth-protected-resource"


class _Response:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class _HttpError(Exception):
    """Stands in for an httpx.HTTPStatusError (same shape: .response)."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.response = _Response(status_code, headers)


def test_a_rejected_credential_is_auth_required_with_its_metadata_url() -> None:
    error = _HttpError(401, {"WWW-Authenticate": f'Bearer resource_metadata="{_METADATA_URL}"'})

    failure = classify_mcp_error(error, server="acme-kb")

    assert failure.reason is McpFailureReason.AUTH_REQUIRED
    assert failure.status_code == 401
    assert failure.resource_metadata == _METADATA_URL
    assert "re-authorize" in failure.message


def test_forbidden_is_also_auth_required_without_a_challenge() -> None:
    failure = classify_mcp_error(_HttpError(403), server="acme-kb")

    assert failure.reason is McpFailureReason.AUTH_REQUIRED
    assert failure.resource_metadata is None


def test_a_server_error_is_http_error() -> None:
    failure = classify_mcp_error(_HttpError(500), server="acme-kb")

    assert failure.reason is McpFailureReason.HTTP_ERROR
    assert failure.status_code == 502
    assert "500" in failure.message


def test_a_transport_failure_is_unreachable() -> None:
    failure = classify_mcp_error(ConnectionError("dns lookup failed"), server="acme-kb")

    assert failure.reason is McpFailureReason.UNREACHABLE
    assert failure.status_code == 502
    assert "dns lookup failed" in failure.message


def test_a_deadline_is_a_timeout() -> None:
    failure = classify_mcp_error(TimeoutError("deadline"), server="acme-kb")

    assert failure.reason is McpFailureReason.TIMEOUT
    assert failure.status_code == 504


def test_an_open_breaker_is_its_own_reason() -> None:
    failure = classify_mcp_error(CircuitOpenError("open for mcp:acme-kb:abc"), server="acme-kb")

    assert failure.reason is McpFailureReason.CIRCUIT_OPEN
    assert failure.status_code == 503


def test_a_certificate_failure_is_its_own_reason() -> None:
    """TLS has a different fix from "server down" — say which one it is."""
    import ssl

    error = ssl.SSLCertVerificationError(1, "certificate verify failed: self-signed certificate")

    failure = classify_mcp_error(error, server="acme-kb")

    assert failure.reason is McpFailureReason.TLS_ERROR
    assert failure.status_code == 502
    assert "CUSTOM_CA_PATH" in failure.message


def test_a_wrapped_certificate_failure_is_still_recognised() -> None:
    import ssl

    try:
        try:
            raise ssl.SSLCertVerificationError(1, "certificate verify failed")
        except ssl.SSLError as inner:
            raise ConnectionError("connection failed") from inner
    except ConnectionError as outer:
        failure = classify_mcp_error(outer, server="acme-kb")

    assert failure.reason is McpFailureReason.TLS_ERROR


def test_a_plain_transport_failure_is_not_blamed_on_tls() -> None:
    """A generic handshake failure must not send the operator to the cert."""
    failure = classify_mcp_error(RuntimeError("handshake failed: connection reset"), server="acme-kb")

    assert failure.reason is McpFailureReason.UNREACHABLE


def test_classification_walks_the_cause_chain() -> None:
    """Adapters wrap transport errors; the useful signal is at the bottom."""
    try:
        try:
            raise _HttpError(401)
        except _HttpError as inner:
            raise RuntimeError("handshake failed") from inner
    except RuntimeError as outer:
        failure = classify_mcp_error(outer, server="acme-kb")

    assert failure.reason is McpFailureReason.AUTH_REQUIRED


def test_a_self_referential_chain_terminates() -> None:
    error = RuntimeError("loop")
    error.__cause__ = error

    failure = classify_mcp_error(error, server="acme-kb")

    assert failure.reason is McpFailureReason.UNREACHABLE


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (McpFailureReason.UNREACHABLE, 502),
        (McpFailureReason.TIMEOUT, 504),
        (McpFailureReason.AUTH_REQUIRED, 401),
        (McpFailureReason.HTTP_ERROR, 502),
        (McpFailureReason.CIRCUIT_OPEN, 503),
        (McpFailureReason.INVALID_CONFIG, 500),
    ],
)
def test_every_reason_has_a_status(reason: McpFailureReason, expected: int) -> None:
    from agent_server.repo.graphs.mcp_errors import McpFailure

    assert McpFailure(server="s", reason=reason, message="m").status_code == expected


def test_detail_shape_carries_the_reason_and_optional_metadata() -> None:
    with_metadata = classify_mcp_error(
        _HttpError(401, {"WWW-Authenticate": f'Bearer resource_metadata="{_METADATA_URL}"'}), server="acme-kb"
    )
    without = classify_mcp_error(ConnectionError("nope"), server="acme-kb")

    detail: dict[str, Any] = with_metadata.as_detail()
    assert detail == {
        "message": with_metadata.message,
        "reason": "auth_required",
        "server": "acme-kb",
        "resource_metadata": _METADATA_URL,
    }
    assert "resource_metadata" not in without.as_detail()
