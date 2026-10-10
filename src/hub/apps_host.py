"""MCP Apps host proxy helpers: request-scoped MCP sessions per HTTP request.

Every operation opens a short-lived session against the caller's own
connection (OAuth-aware — the provider builds the per-user auth) and closes
it afterwards. Nothing is cached in process memory, so any pod can serve any
request (same multi-pod rule as the rest of the hub).

Two deliberate differences from the graph-side loader:

- **No model-facing filter.** ``mcp_loader.load_mcp_tools`` drops app-only
  tools when MCP Apps is enabled; the host proxy exists to reach exactly
  those, so it lists the server's tools unfiltered.
- **No tool cache.** The cache holds model-facing tool objects; app-only
  tools are not in it, and a per-request session is already short-lived.

The circuit breaker *is* shared with the graph side: same endpoint, same
failure state.

Authorization is not done here — callers (``McpConnectionService``) resolve
the caller's own row and run the policy engine before handing it over.
"""

import asyncio
import os
from typing import Any, cast

import structlog
from fastapi import HTTPException

from agent_server.config.settings import settings
from agent_server.repo.graphs.mcp_apps import ensure_mcp_apps_capability_advertised
from agent_server.repo.graphs.mcp_loader import mcp_breaker
from hub.db import McpConnection as McpConnectionORM
from hub.oauth.flow import build_oauth_auth

logger = structlog.getLogger(__name__)


def _connection_map(connection: McpConnectionORM) -> dict[str, Any]:
    """langchain-mcp-adapters connection object for one resolved row."""
    conn: dict[str, Any] = {"transport": connection.transport, "url": connection.url}
    if connection.auth_type == "oauth":
        conn["auth"] = build_oauth_auth(connection.user_id, connection.name, connection.url)
    elif connection.auth_type == "headers" and connection.headers:
        conn["headers"] = connection.headers
    return conn


def _client_for(connection: McpConnectionORM) -> Any:
    """MultiServerMCPClient for one resolved connection row."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    # Same one-time patch the graph-side loader installs: without it the
    # handshake never advertises MCP Apps, so the server returns no _meta.ui
    # and the proxy (whose whole purpose is that metadata) sees plain tools.
    if settings.mcp.MCP_APPS_ENABLED:
        ensure_mcp_apps_capability_advertised()
    return MultiServerMCPClient(cast("Any", {connection.name: _connection_map(connection)}))


async def _run_guarded(connection: McpConnectionORM, operation: Any) -> Any:
    """Run *operation* under the endpoint's breaker and the load timeout.

    One call = one user-visible operation, so a failure anywhere in it counts
    once against the breaker (same as a graph-side load). Client errors (an
    unknown tool, say) must therefore be *returned* by the operation, not
    raised — a bad tool name is not the server flapping.
    """
    try:
        async with asyncio.timeout(_timeout()):
            return await mcp_breaker(connection.name, _connection_map(connection)).call(operation)
    except TimeoutError as e:
        raise HTTPException(status_code=502, detail=f"MCP server {connection.name!r} timed out") from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"MCP server {connection.name!r} unreachable: {str(e)[:300]}"
        ) from e


def _timeout() -> float:
    """Bound host proxy operations (same env as graph-side loads)."""
    return float(os.environ.get("MCP_LOAD_TIMEOUT_SECS", "15"))


async def list_tools(connection: McpConnectionORM) -> list[dict[str, Any]]:
    """The connection's tools with their metadata (incl. _meta.ui)."""

    async def _list() -> list[dict[str, Any]]:
        tools = await _client_for(connection).get_tools()
        return [{"name": t.name, "description": t.description, "metadata": t.metadata or {}} for t in tools]

    return await _run_guarded(connection, _list)


async def call_tool(connection: McpConnectionORM, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Invoke one tool by name; content and artifact pass through."""

    async def _call() -> dict[str, Any] | None:
        tools = await _client_for(connection).get_tools()
        tool = next((t for t in tools if t.name == tool_name or t.name.endswith(f"_{tool_name}")), None)
        if tool is None:
            return None  # returned, not raised: a bad name must not trip the breaker
        result = await tool.ainvoke(args)
        # adapters return (content, artifact) for content_and_artifact tools,
        # but plain single-block results come back unpaired.
        content, artifact = result if isinstance(result, tuple) and len(result) == 2 else (result, None)
        return {"content": content, "artifact": artifact}

    result = await _run_guarded(connection, _call)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Tool {tool_name!r} not found on {connection.name!r}")
    return result


async def list_resources(connection: McpConnectionORM) -> dict[str, Any]:
    """The connection's resources and resource templates."""

    async def _list() -> dict[str, Any]:
        async with _client_for(connection).session(connection.name) as mcp:
            resources = await mcp.list_resources()
            templates = await mcp.list_resource_templates()
        return {
            "resources": [r.model_dump(mode="json") for r in resources.resources],
            "resource_templates": [t.model_dump(mode="json") for t in templates.resourceTemplates],
        }

    return await _run_guarded(connection, _list)


async def read_resource(connection: McpConnectionORM, uri: str) -> dict[str, Any]:
    """Read one resource by URI; text contents only (binary blobs omitted)."""

    async def _read() -> dict[str, Any]:
        async with _client_for(connection).session(connection.name) as mcp:
            result = await mcp.read_resource(uri)
        return {
            "contents": [
                item.model_dump(mode="json") for item in result.contents if getattr(item, "text", None) is not None
            ]
        }

    return await _run_guarded(connection, _read)
