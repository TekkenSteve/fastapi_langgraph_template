"""MCP debug surface: inspect the registry and probe servers live.

The graph-side loader degrades gracefully (zero tools on failure) — which
means a misconfigured MCP server is silent at runtime. This router makes the
state inspectable: which servers are registered, and what each answers.

Registered only in LOCAL/dev mode: it reveals internal topology (server
names, tool names, error details) and must not ship to production.
"""

import sys
from typing import Any, cast

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from agent_server.config.graph_config import load_mcp_servers_config

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])


class McpServerStatus(BaseModel):
    name: str
    status: str  # "ok" | "error"
    tools: list[str] = []
    error: str | None = None


@router.get("/servers")
async def list_mcp_servers() -> list[McpServerStatus]:
    """List every registered MCP server with a live probe of its tools."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    results: list[McpServerStatus] = []
    for name, conn in load_mcp_servers_config().items():
        conn = dict(conn)
        if conn.get("command") == "python":
            conn["command"] = sys.executable  # same rule as the graph-side loader
        try:
            tools = await MultiServerMCPClient(cast("Any", {name: conn})).get_tools()
            results.append(McpServerStatus(name=name, status="ok", tools=[t.name for t in tools]))
        except Exception as e:
            results.append(McpServerStatus(name=name, status="error", error=str(e)[:300]))
    return results
