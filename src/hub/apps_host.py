"""MCP Apps host proxy helpers: request-scoped MCP sessions per HTTP request.

Every operation opens a short-lived session against the caller's own
connection (OAuth-aware — the provider builds the per-user auth) and closes
it afterwards. Nothing is cached in process memory, so any pod can serve any
request (same multi-pod rule as the rest of the hub).
"""

import asyncio
import os
from typing import Any, cast

import structlog
from fastapi import HTTPException

from hub.oauth.flow import build_oauth_auth
from hub.repositories import McpConnectionRepository

logger = structlog.getLogger(__name__)


def _client_for(connection: Any) -> Any:
    """MultiServerMCPClient for one resolved connection row."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    conn: dict[str, Any] = {"transport": connection.transport, "url": connection.url}
    if connection.auth_type == "oauth":
        conn["auth"] = build_oauth_auth(connection.user_id, connection.name, connection.url)
    elif connection.auth_type == "headers" and connection.headers:
        conn["headers"] = connection.headers
    return MultiServerMCPClient(cast("Any", {connection.name: conn}))  # conn shape is runtime-validated


async def _resolve(repo: McpConnectionRepository, user_id: str, name: str) -> Any:
    connection = await repo.get_for_owner(user_id, name)
    if connection is None:
        raise HTTPException(status_code=404, detail=f"MCP connection {name!r} not found")
    return connection


def _timeout() -> float:
    """Bound host proxy operations (same env as graph-side loads)."""
    return float(os.environ.get("MCP_LOAD_TIMEOUT_SECS", "15"))


async def list_tools(repo: McpConnectionRepository, user_id: str, name: str) -> list[dict[str, Any]]:
    """The connection's tools with their metadata (incl. _meta.ui)."""
    connection = await _resolve(repo, user_id, name)
    try:
        async with asyncio.timeout(_timeout()):
            tools = await _client_for(connection).get_tools()
    except TimeoutError as e:
        raise HTTPException(status_code=502, detail=f"MCP server {name!r} timed out") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MCP server {name!r} unreachable: {str(e)[:300]}") from e
    return [
        {
            "name": t.name,
            "description": t.description,
            "metadata": t.metadata or {},
        }
        for t in tools
    ]


async def call_tool(
    repo: McpConnectionRepository,
    user_id: str,
    name: str,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Invoke one tool by name; content and artifact pass through."""
    connection = await _resolve(repo, user_id, name)
    try:
        async with asyncio.timeout(_timeout()):
            tools = await _client_for(connection).get_tools()
            tool = next((t for t in tools if t.name == tool_name or t.name.endswith(f"_{tool_name}")), None)
            if tool is None:
                raise HTTPException(status_code=404, detail=f"Tool {tool_name!r} not found on {name!r}")
            result = await tool.ainvoke(args)
            # adapters return (content, artifact) for content_and_artifact
            # tools, but plain single-block results come back unpaired.
            content, artifact = result if isinstance(result, tuple) and len(result) == 2 else (result, None)
    except HTTPException:
        raise
    except TimeoutError as e:
        raise HTTPException(status_code=502, detail=f"MCP server {name!r} timed out") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MCP tool call failed: {str(e)[:300]}") from e
    return {"content": content, "artifact": artifact}


async def list_resources(repo: McpConnectionRepository, user_id: str, name: str) -> dict[str, Any]:
    """The connection's resources and resource templates."""
    connection = await _resolve(repo, user_id, name)
    try:
        async with asyncio.timeout(_timeout()):
            async with _client_for(connection).session(name) as mcp:
                resources = await mcp.list_resources()
                templates = await mcp.list_resource_templates()
    except TimeoutError as e:
        raise HTTPException(status_code=502, detail=f"MCP server {name!r} timed out") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MCP server {name!r} unreachable: {str(e)[:300]}") from e
    return {
        "resources": [r.model_dump(mode="json") for r in resources.resources],
        "resource_templates": [t.model_dump(mode="json") for t in templates.resourceTemplates],
    }


async def read_resource(repo: McpConnectionRepository, user_id: str, name: str, uri: str) -> dict[str, Any]:
    """Read one resource by URI; text contents only (binary blobs omitted)."""
    connection = await _resolve(repo, user_id, name)
    try:
        async with asyncio.timeout(_timeout()):
            async with _client_for(connection).session(name) as mcp:
                result = await mcp.read_resource(uri)
    except TimeoutError as e:
        raise HTTPException(status_code=502, detail=f"MCP server {name!r} timed out") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MCP resource read failed: {str(e)[:300]}") from e
    return {
        "contents": [
            item.model_dump(mode="json") for item in result.contents if getattr(item, "text", None) is not None
        ]
    }
