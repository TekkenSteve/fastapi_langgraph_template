"""Unit tests for the skill fetcher: SSRF gate + manual redirect handling.

All URLs use public IP literals so the guard needs no DNS, and the HTTP layer
is an ``httpx.MockTransport`` so nothing touches the network.
"""

import httpx
import pytest

from agent_server.infra import skill_fetcher
from agent_server.infra.skill_fetcher import SkillFetchError, fetch_bytes

# Public literals: the guard passes them without a DNS lookup.
_PUBLIC_A = "http://93.184.216.34"
_PUBLIC_B = "http://1.1.1.1"


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Route every AsyncClient the fetcher creates through a MockTransport."""
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    def factory(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(recording_handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(skill_fetcher.httpx, "AsyncClient", factory)
    return requests


class TestFetchBytes:
    async def test_fetches_body_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_transport(monkeypatch, lambda req: httpx.Response(200, content=b"skill-bytes"))

        assert await fetch_bytes(f"{_PUBLIC_A}/SKILL.md", max_bytes=100, timeout=5) == b"skill-bytes"

    async def test_non_200_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_transport(monkeypatch, lambda req: httpx.Response(404))

        with pytest.raises(SkillFetchError, match="HTTP 404"):
            await fetch_bytes(f"{_PUBLIC_A}/SKILL.md", max_bytes=100, timeout=5)

    async def test_size_cap_is_enforced_mid_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_transport(monkeypatch, lambda req: httpx.Response(200, content=b"x" * 50))

        with pytest.raises(SkillFetchError, match="exceeds"):
            await fetch_bytes(f"{_PUBLIC_A}/SKILL.md", max_bytes=10, timeout=5)

    async def test_private_target_is_refused_before_any_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests = _install_transport(monkeypatch, lambda req: httpx.Response(200, content=b"meta"))

        with pytest.raises(SkillFetchError, match="private or reserved"):
            await fetch_bytes("http://169.254.169.254/latest/meta-data", max_bytes=100, timeout=5)
        assert requests == []  # the gate runs before the client is used

    async def test_non_http_scheme_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_transport(monkeypatch, lambda req: httpx.Response(200))

        with pytest.raises(SkillFetchError, match="scheme must be http or https"):
            await fetch_bytes("file:///etc/passwd", max_bytes=100, timeout=5)


class TestRedirectHandling:
    async def test_follows_redirects_between_public_targets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/hop":
                return httpx.Response(302, headers={"location": f"{_PUBLIC_B}/final"})
            return httpx.Response(200, content=b"arrived")

        requests = _install_transport(monkeypatch, handler)

        assert await fetch_bytes(f"{_PUBLIC_A}/hop", max_bytes=100, timeout=5) == b"arrived"
        assert [str(r.url) for r in requests] == [f"{_PUBLIC_A}/hop", f"{_PUBLIC_B}/final"]

    async def test_redirect_into_private_space_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A public URL 302-ing to the metadata endpoint is the classic SSRF relay."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

        requests = _install_transport(monkeypatch, handler)

        with pytest.raises(SkillFetchError, match="private or reserved"):
            await fetch_bytes(f"{_PUBLIC_A}/hop", max_bytes=100, timeout=5)
        assert len(requests) == 1  # the second hop never fired

    async def test_redirect_to_a_disallowed_scheme_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "file:///etc/passwd"})

        _install_transport(monkeypatch, handler)

        with pytest.raises(SkillFetchError, match="scheme must be http or https"):
            await fetch_bytes(f"{_PUBLIC_A}/hop", max_bytes=100, timeout=5)

    async def test_redirect_without_location_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_transport(monkeypatch, lambda req: httpx.Response(302))

        with pytest.raises(SkillFetchError, match="no Location header"):
            await fetch_bytes(f"{_PUBLIC_A}/hop", max_bytes=100, timeout=5)

    async def test_redirect_loop_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": f"{_PUBLIC_A}/loop"})

        requests = _install_transport(monkeypatch, handler)

        with pytest.raises(SkillFetchError, match="redirects"):
            await fetch_bytes(f"{_PUBLIC_A}/loop", max_bytes=100, timeout=5)
        assert len(requests) == skill_fetcher._MAX_REDIRECT_HOPS + 1
