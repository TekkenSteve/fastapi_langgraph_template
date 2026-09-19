"""Unit tests for MCP connection hub domain validation."""

import pytest
from pydantic import ValidationError

from hub.models import (
    McpConnectionCreate,
    McpConnectionValidationError,
    validate_connection_name,
    validate_connection_url,
)


@pytest.mark.parametrize("name", ["acme-kb", "a", "kb2", "a" * 64])
def test_accepts_valid_names(name: str) -> None:
    validate_connection_name(name)


@pytest.mark.parametrize("name", ["", "Bad_Name", "-leading", "x" * 65, "has space"])
def test_rejects_invalid_names(name: str) -> None:
    with pytest.raises(McpConnectionValidationError, match="name"):
        validate_connection_name(name)


@pytest.mark.parametrize("url", ["https://mcp.example.com/kb", "http://localhost:8080/mcp"])
def test_accepts_http_urls(url: str) -> None:
    validate_connection_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "mcp.example.com", "ftp://x.com", "https://", ""])
def test_rejects_non_http_urls(url: str) -> None:
    with pytest.raises(McpConnectionValidationError, match="http"):
        validate_connection_url(url)


def test_stdio_transport_is_rejected_by_the_model() -> None:
    """stdio is a deployment privilege — the user-tier model pins the only
    allowed transport so a crafted payload cannot smuggle a command in."""
    with pytest.raises(ValidationError):
        McpConnectionCreate(name="ok", transport="stdio", url="https://x.com")  # type: ignore[arg-type]


def test_header_count_is_capped() -> None:
    with pytest.raises(ValidationError):
        McpConnectionCreate(name="ok", url="https://x.com", headers={f"h{i}": "v" for i in range(21)})


# --- auth_type validation -----------------------------------------------------


def test_auth_type_defaults_to_none() -> None:
    conn = McpConnectionCreate(name="ok", url="https://x.com")
    assert conn.auth_type == "none"


def test_headers_auth_type_requires_headers() -> None:
    with pytest.raises(ValidationError, match="requires at least one header"):
        McpConnectionCreate(name="ok", url="https://x.com", auth_type="headers")


def test_static_headers_rejected_for_other_auth_types() -> None:
    with pytest.raises(ValidationError, match="not allowed"):
        McpConnectionCreate(name="ok", url="https://x.com", auth_type="oauth", headers={"A": "b"})
    with pytest.raises(ValidationError, match="not allowed"):
        McpConnectionCreate(name="ok", url="https://x.com", headers={"A": "b"})


def test_oauth_auth_type_accepted_without_headers() -> None:
    conn = McpConnectionCreate(name="ok", url="https://x.com", auth_type="oauth")
    assert conn.auth_type == "oauth"
