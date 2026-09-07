"""MCP tools for graphs: registry resolution, loading, and the wrapper.

Model: `langgraph.json` declares which MCP servers exist (top-level
``mcp_servers`` registry); a graph composes them in one line:

    from agent_server.contracts import with_mcp_tools
    graph = with_mcp_tools(build_my_agent, servers=["acme-kb"])

``with_mcp_tools`` returns a plain 0-arg async factory — the framework loads
it like any other factory and needs zero MCP knowledge.

- Ops override: ``MCP_SERVER__<NAME>`` env var (JSON connection object)
  replaces that registry entry for this deployment.
- Tool names are namespaced by server name, ``python`` stdio commands
  normalize to the current interpreter, and failures degrade to zero
  tools — MCP is additive and must never break graph startup.
"""

import asyncio
import inspect
import json
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from langchain_core.tools import BaseTool

from agent_server.config.graph_config import load_mcp_servers_config

logger = structlog.get_logger(__name__)


def _normalize_command(connections: dict[str, Any]) -> dict[str, Any]:
    """``python`` as a stdio command resolves to the current interpreter —
    correct in dev venvs and containers alike (production images have no .venv)."""
    for conn in connections.values():
        if isinstance(conn, dict) and conn.get("command") == "python":
            conn["command"] = sys.executable
    return connections


def resolve_mcp_connections(server_names: list[str]) -> dict[str, Any]:
    """Resolve server names against the deployment registry.

    ``MCP_SERVER__<NAME>`` (JSON connection object) overrides that entry.
    Unknown names are skipped with a warning.
    """
    registry = load_mcp_servers_config()
    connections: dict[str, Any] = {}
    for name in server_names:
        raw = os.environ.get(f"MCP_SERVER__{name.upper().replace('-', '_')}", "").strip()
        if raw:
            try:
                conn = json.loads(raw)
                if isinstance(conn, dict):
                    connections[name] = conn
                    continue
                logger.warning("mcp_env_not_an_object", server=name)
            except json.JSONDecodeError as e:
                logger.warning("mcp_env_invalid_json", server=name, error=str(e))
        if name not in registry:
            logger.warning("mcp_server_not_in_registry", server=name)
            continue
        connections[name] = registry[name]
    return _normalize_command(connections)


async def load_mcp_tools(
    connections: dict[str, Any],
    *,
    interceptors: list | None = None,
) -> list[BaseTool]:
    """Load tools from MCP servers given a connections map.

    Args:
        connections: langchain-mcp-adapters connection map.
        interceptors: optional ToolCallInterceptor chain (retry/cache/logging).

    Returns:
        Tools from all reachable servers, namespaced by server name;
        empty list on any failure.
    """
    if not connections:
        return []
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        # Bound the handshake: a hung MCP server must degrade, not stall startup.
        timeout = float(os.environ.get("MCP_LOAD_TIMEOUT_SECS", "15"))
        client = MultiServerMCPClient(connections, tool_interceptors=interceptors, tool_name_prefix=True)
        async with asyncio.timeout(timeout):
            tools = await client.get_tools()
        logger.info("mcp_tools_loaded", servers=list(connections), tools=[t.name for t in tools])
        return list(tools)
    except TimeoutError:
        logger.warning("mcp_tools_load_timeout", servers=list(connections))
        return []
    except Exception as e:
        logger.warning("mcp_tools_load_failed", error=str(e))
        return []


def with_mcp_tools(
    build: Callable[..., Any],
    servers: list[str],
    *,
    tools_param: str = "mcp_tools",
) -> Callable[[], Awaitable[Any]]:
    """Wrap a graph builder: resolve MCP tools from the registry, then build.

    Returns a 0-arg async factory — the framework loads it like any other
    graph factory. The builder receives the tools under ``tools_param``
    (empty list when nothing resolved).
    """

    async def factory() -> Any:
        tools = await load_mcp_tools(resolve_mcp_connections(servers))
        result = build(**{tools_param: tools})
        # generate_graph dispatches the factory's return type only once — an
        # async builder's coroutine must be awaited here to not leak through.
        if inspect.isawaitable(result):
            return await result
        return result

    return factory
