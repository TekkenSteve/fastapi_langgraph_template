"""Unit tests for the MCP Apps host proxy helpers (apps_host.py).

The adapter client is faked at ``_client_for`` — the point here is the
guard behavior: what reaches the wire, what counts against the endpoint's
circuit breaker, and that app-only tools are not filtered out.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import agent_server.repo.graphs.mcp_loader as loader
from hub import apps_host


class _Tool:
    def __init__(self, name: str, metadata: dict[str, Any] | None = None) -> None:
        self.name = name
        self.description = f"{name} tool"
        self.metadata = metadata or {}

    async def ainvoke(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": args}


class _Client:
    def __init__(self, tools: list[_Tool] | Exception) -> None:
        self._tools = tools

    async def get_tools(self) -> list[_Tool]:
        if isinstance(self._tools, Exception):
            raise self._tools
        return self._tools


def _row(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "user_id": "u1",
        "name": "acme-kb",
        "transport": "streamable_http",
        "url": "https://mcp.example.com/kb",
        "auth_type": "none",
        "headers": {},
        "enabled": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture(autouse=True)
def _breakers() -> None:
    loader.reset_mcp_breakers()


async def test_list_tools_keeps_app_only_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proxy is the UI half: filtering here would hide exactly what it serves."""
    app_only = _Tool("ui-only", {"_meta": {"ui": {"visibility": ["app"]}}})
    monkeypatch.setattr(loader.settings.mcp, "MCP_APPS_ENABLED", True)
    monkeypatch.setattr(apps_host, "_client_for", lambda _row: _Client([_Tool("model-tool"), app_only]))

    tools = await apps_host.list_tools(_row())

    assert [t["name"] for t in tools] == ["model-tool", "ui-only"]


async def test_unknown_tool_is_404_without_tripping_the_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader.settings.mcp, "MCP_BREAKER_THRESHOLD", 1)
    monkeypatch.setattr(apps_host, "_client_for", lambda _row: _Client([_Tool("render")]))

    with pytest.raises(HTTPException) as exc:
        await apps_host.call_tool(_row(), "nope", {})

    assert exc.value.status_code == 404
    state = (await loader.get_mcp_breaker_states())["acme-kb"]
    assert state["failures"] == 0
    assert state["open"] is False


async def test_transport_failure_is_502_and_counts_against_the_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader.settings.mcp, "MCP_BREAKER_THRESHOLD", 1)
    monkeypatch.setattr(apps_host, "_client_for", lambda _row: _Client(ConnectionError("refused")))

    with pytest.raises(HTTPException) as exc:
        await apps_host.list_tools(_row())

    assert exc.value.status_code == 502
    state = (await loader.get_mcp_breaker_states())["acme-kb"]
    assert state["open"] is True


async def test_tool_content_and_artifact_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    class _PairTool(_Tool):
        async def ainvoke(self, args: dict[str, Any]) -> tuple[Any, Any]:
            return [{"type": "text", "text": "ok"}], {"structured_content": {"x": 1}}

    monkeypatch.setattr(apps_host, "_client_for", lambda _row: _Client([_PairTool("render")]))

    result = await apps_host.call_tool(_row(), "render", {"a": 1})

    assert result["content"] == [{"type": "text", "text": "ok"}]
    assert result["artifact"] == {"structured_content": {"x": 1}}


def test_client_creation_advertises_the_apps_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy-first request must still get _meta.ui back from the server."""
    installed: list[bool] = []
    monkeypatch.setattr(apps_host, "ensure_mcp_apps_capability_advertised", lambda: installed.append(True))
    monkeypatch.setattr(loader.settings.mcp, "MCP_APPS_ENABLED", True)

    apps_host._client_for(_row())

    assert installed == [True]


def test_client_creation_skips_the_patch_when_apps_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[bool] = []
    monkeypatch.setattr(apps_host, "ensure_mcp_apps_capability_advertised", lambda: installed.append(True))
    monkeypatch.setattr(loader.settings.mcp, "MCP_APPS_ENABLED", False)

    apps_host._client_for(_row())

    assert installed == []
