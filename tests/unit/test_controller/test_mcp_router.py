"""MCP debug router (controller/http/routers/mcp.py)."""

import sys
from pathlib import Path

import agent_server.controller.http.routers.mcp as mcp_router_mod
from agent_server.controller.http.routers.mcp import list_mcp_servers


async def test_empty_registry_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(mcp_router_mod, "load_mcp_servers_config", lambda: {})
    monkeypatch.setattr(mcp_router_mod, "env_override_names", lambda: [])
    assert await list_mcp_servers() == []


async def test_erroring_server_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_router_mod,
        "load_mcp_servers_config",
        lambda: {"broken": {"transport": "stdio", "command": "false", "args": []}},
    )
    monkeypatch.setattr(mcp_router_mod, "env_override_names", lambda: [])
    results = await list_mcp_servers()
    assert len(results) == 1
    assert results[0].status == "error"
    assert results[0].error
    assert results[0].source == "registry"


async def test_healthy_server_lists_tools(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setattr(
        mcp_router_mod,
        "load_mcp_servers_config",
        lambda: {
            "acme-kb": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["src/shop/mcp_server.py"],
                "cwd": str(root),
            }
        },
    )
    monkeypatch.setattr(mcp_router_mod, "env_override_names", lambda: [])
    results = await list_mcp_servers()
    assert results[0].status == "ok"
    assert any("kb_search" in t for t in results[0].tools)


async def test_env_override_applies_to_probe(monkeypatch) -> None:
    """The probe resolves through the same path as runtime: an env override
    must be visible in the result, not the stale registry entry."""
    monkeypatch.setattr(
        mcp_router_mod,
        "load_mcp_servers_config",
        lambda: {"acme-kb": {"transport": "stdio", "command": "false", "args": []}},
    )
    monkeypatch.setattr(mcp_router_mod, "env_override_names", lambda: [])
    monkeypatch.setenv("MCP_SERVER__ACME_KB", '{"transport": "stdio", "command": "false", "args": []}')
    # registry entry AND env both broken here — but resolution must not fall
    # back silently: the probe should reflect the env override path.
    results = await list_mcp_servers()
    assert results[0].status == "error"


async def test_env_only_server_appears_with_env_source(monkeypatch) -> None:
    monkeypatch.setattr(mcp_router_mod, "load_mcp_servers_config", lambda: {})
    monkeypatch.setattr(mcp_router_mod, "env_override_names", lambda: ["extra-srv"])
    monkeypatch.setenv("MCP_SERVER__EXTRA_SRV", '{"transport": "stdio", "command": "false", "args": []}')
    results = await list_mcp_servers()
    assert results[0].name == "extra-srv"
    assert results[0].source == "env"


async def test_breaker_state_surfaced(monkeypatch) -> None:
    async def fake_states():
        return {"broken": {"failures": 5, "open": True, "open_for_secs": 42.0}}

    monkeypatch.setattr(
        mcp_router_mod,
        "load_mcp_servers_config",
        lambda: {"broken": {"transport": "stdio", "command": "false", "args": []}},
    )
    monkeypatch.setattr(mcp_router_mod, "env_override_names", lambda: [])
    monkeypatch.setattr(mcp_router_mod, "get_mcp_breaker_states", fake_states)
    results = await list_mcp_servers()
    assert results[0].breaker == "open (42.0s remaining)"
