"""MCP loader: registry resolution, env override, degradation, wrapper."""

import sys
from pathlib import Path

import pytest

import agent_server.repo.graphs.mcp_loader as loader
from agent_server.repo.graphs.mcp_loader import load_mcp_tools, resolve_mcp_connections, with_mcp_tools

_REGISTRY = {
    "acme-kb": {
        "transport": "stdio",
        "command": "python",  # loader normalizes to sys.executable
        "args": ["src/shop/mcp_server.py"],
        "cwd": str(Path(__file__).resolve().parents[3]),
    }
}


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    monkeypatch.setattr(loader, "load_mcp_servers_config", lambda: dict(_REGISTRY))


def test_unknown_server_name_skipped_with_warning() -> None:
    assert list(resolve_mcp_connections(["acme-kb", "nonexistent"])) == ["acme-kb"]


def test_python_command_resolves_to_current_interpreter() -> None:
    assert resolve_mcp_connections(["acme-kb"])["acme-kb"]["command"] == sys.executable


def test_env_override_replaces_registry_entry(monkeypatch) -> None:
    monkeypatch.setenv("MCP_SERVER__ACME_KB", '{"transport": "stdio", "command": "true"}')
    assert resolve_mcp_connections(["acme-kb"]) == {"acme-kb": {"transport": "stdio", "command": "true"}}


def test_invalid_env_falls_back_to_registry(monkeypatch) -> None:
    monkeypatch.setenv("MCP_SERVER__ACME_KB", "not-json")
    assert resolve_mcp_connections(["acme-kb"])["acme-kb"]["command"] == sys.executable


async def test_empty_loads_nothing() -> None:
    assert await load_mcp_tools({}) == []


async def test_unreachable_server_degrades_gracefully() -> None:
    assert await load_mcp_tools({"nope": {"transport": "stdio", "command": "false", "args": []}}) == []


async def test_wrapper_resolves_and_builds() -> None:
    """with_mcp_tools: 0-arg async factory that builds with resolved tools."""
    captured = {}

    def build(mcp_tools):
        captured["tools"] = mcp_tools
        return "compiled"

    factory = with_mcp_tools(build, servers=["acme-kb"])
    result = await factory()
    assert result == "compiled"
    assert any("kb_search" in t.name for t in captured["tools"])


async def test_wrapper_unknown_servers_build_with_empty_tools() -> None:
    captured = {}

    def build(mcp_tools):
        captured["tools"] = mcp_tools
        return "compiled"

    result = await with_mcp_tools(build, servers=["nonexistent"])()
    assert result == "compiled"
    assert captured["tools"] == []


async def test_wrapper_awaits_async_builders() -> None:
    """An async builder's coroutine must not leak through the wrapper
    (generate_graph dispatches the factory's return type only once)."""
    captured = {}

    async def build(mcp_tools):
        captured["tools"] = mcp_tools
        return "compiled"

    result = await with_mcp_tools(build, servers=[])()
    assert result == "compiled"
    assert captured["tools"] == []


async def test_hanging_server_times_out_not_stalls(monkeypatch) -> None:
    """A hung MCP handshake degrades within the load timeout."""
    import asyncio

    monkeypatch.setenv("MCP_LOAD_TIMEOUT_SECS", "0.1")

    async def _hang(self):
        await asyncio.sleep(60)

    from langchain_mcp_adapters.client import MultiServerMCPClient

    monkeypatch.setattr(MultiServerMCPClient, "get_tools", _hang)
    tools = await load_mcp_tools({"hung": {"transport": "stdio", "command": "true"}})
    assert tools == []
