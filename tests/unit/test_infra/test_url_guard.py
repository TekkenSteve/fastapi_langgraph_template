"""Unit tests for the SSRF URL guard (no network access).

Literal-IP cases need no DNS. Hostname cases stub ``_resolve`` — the guard's
only network touch — so the suite runs hermetically.
"""

import ipaddress
from unittest.mock import Mock

import pytest

from agent_server.infra import url_guard
from agent_server.infra.url_guard import UrlGuardError, validate_public_http_url


def _addr(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(text)


class TestSchemeAndHost:
    async def test_https_and_http_are_allowed_schemes(self) -> None:
        assert await validate_public_http_url("http://93.184.216.34/skill.md") == "http://93.184.216.34/skill.md"
        assert await validate_public_http_url("https://93.184.216.34/skill.md") == "https://93.184.216.34/skill.md"

    @pytest.mark.parametrize("scheme", ["file", "ftp", "gopher", "data", "ws", ""])
    async def test_other_schemes_are_refused(self, scheme: str) -> None:
        with pytest.raises(UrlGuardError, match="scheme must be http or https"):
            await validate_public_http_url(f"{scheme}://93.184.216.34/x")

    async def test_missing_host_is_refused(self) -> None:
        with pytest.raises(UrlGuardError, match="no host"):
            await validate_public_http_url("http:///just/a/path")


def _url(host: str) -> str:
    """Build a fetch URL, bracketing IPv6 literals as URL syntax requires."""
    return f"https://[{host}]/skill" if ":" in host else f"https://{host}/skill"


class TestLiteralIps:
    @pytest.mark.parametrize(
        "host",
        [
            "93.184.216.34",  # public
            "1.1.1.1",
            "2606:4700::1111",  # public IPv6
        ],
    )
    async def test_public_literals_pass(self, host: str) -> None:
        assert await validate_public_http_url(_url(host)) == _url(host)

    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",  # loopback
            "10.0.0.5",  # RFC1918
            "192.168.1.10",
            "172.16.0.1",
            "169.254.169.254",  # cloud metadata (link-local)
            "0.0.0.0",  # unspecified
            "100.64.1.1",  # CGNAT — is_private only covers it from Python 3.13
            "224.0.0.1",  # multicast
            "240.0.0.1",  # reserved
            "::1",  # IPv6 loopback
            "fe80::1",  # IPv6 link-local
            "fc00::1",  # unique-local
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "::ffff:169.254.169.254",
        ],
    )
    async def test_private_or_reserved_literals_are_refused(self, host: str) -> None:
        with pytest.raises(UrlGuardError, match="private or reserved"):
            await validate_public_http_url(_url(host))


class TestHostnames:
    async def test_public_answers_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(url_guard, "_resolve", Mock(return_value=[_addr("93.184.216.34")]))
        assert await validate_public_http_url("https://example.com/skill.md") == "https://example.com/skill.md"

    async def test_private_answer_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(url_guard, "_resolve", Mock(return_value=[_addr("10.1.2.3")]))
        with pytest.raises(UrlGuardError, match="resolves to private or reserved"):
            await validate_public_http_url("https://internal.example.com/skill.md")

    async def test_mixed_answers_are_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A name mixing public and private records is the bypass a single-record check invites."""
        monkeypatch.setattr(url_guard, "_resolve", Mock(return_value=[_addr("93.184.216.34"), _addr("192.0.2.9")]))
        with pytest.raises(UrlGuardError, match="resolves to private or reserved"):
            await validate_public_http_url("https://rebind.example.com/skill.md")

    async def test_resolution_failure_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(url_guard, "_resolve", Mock(side_effect=OSError("NXDOMAIN")))
        with pytest.raises(UrlGuardError, match="cannot resolve"):
            await validate_public_http_url("https://no-such-host.invalid/skill.md")
