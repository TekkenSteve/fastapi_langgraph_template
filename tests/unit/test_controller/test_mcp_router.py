"""MCP debug router (controller/http/routers/mcp.py)."""

import agent_server.controller.http.routers.mcp as mcp_router_mod
from agent_server.controller.http.routers.mcp import list_mcp_servers


async def test_empty_registry_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(mcp_router_mod, "load_mcp_servers_config", lambda: {})
    assert await list_mcp_servers() == []


async def test_erroring_server_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_router_mod,
        "load_mcp_servers_config",
        lambda: {"broken": {"transport": "stdio", "command": "false", "args": []}},
    )
    results = await list_mcp_servers()
    assert len(results) == 1
    assert results[0].status == "error"
    assert results[0].error


async def test_healthy_server_lists_tools(monkeypatch) -> None:
    import sys
    from pathlib import Path

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
    results = await list_mcp_servers()
    assert results[0].status == "ok"
    assert any("kb_search" in t for t in results[0].tools)
