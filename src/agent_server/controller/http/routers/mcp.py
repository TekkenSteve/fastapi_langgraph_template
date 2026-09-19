"""MCP debug surface: inspect the deployment registry and probe servers live.

The graph-side loader degrades gracefully (zero tools on failure) — which
means a misconfigured MCP server is silent at runtime. This router makes the
state inspectable: which servers are registered, what each answers, and
whether its circuit breaker has tripped.

Probing goes through the same resolution as runtime (env overrides and
command normalization included) — a probe that disagrees with what graphs
actually load would be worse than no probe. Raw probing bypasses the
breaker on purpose: diagnostics must see the server as it is, while the
breaker field shows what runtime would do.

Registered only in LOCAL/dev mode: it reveals internal topology (server
names, tool names, error details) and must not ship to production.
"""

import asyncio

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from agent_server.config.graph_config import load_mcp_servers_config
from agent_server.repo.graphs.mcp_loader import (
    _load_server_tools,
    env_override_names,
    get_mcp_breaker_states,
    resolve_mcp_connections,
    validate_connection_dict,
)

logger = structlog.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])

_PROBE_TIMEOUT_SECS = 10.0


class McpServerStatus(BaseModel):
    name: str
    status: str  # "ok" | "error"
    tools: list[str] = []
    error: str | None = None
    source: str = "registry"  # "registry" | "env" (supplied by MCP_SERVER__* only)
    breaker: str | None = None  # None = never loaded | "closed" | "open (Ns remaining)"


@router.get("/servers")
async def list_mcp_servers() -> list[McpServerStatus]:
    """List every registered (or env-supplied) MCP server with a live probe."""
    registry = load_mcp_servers_config()
    env_only = set(env_override_names()) - set(registry)
    names = sorted(set(registry) | env_only)
    # Runtime-identical resolution: env override + command normalization.
    connections = resolve_mcp_connections(names)
    breakers = await get_mcp_breaker_states()

    async def _probe(name: str) -> McpServerStatus:
        breaker = breakers.get(name)
        breaker_label = None
        if breaker is not None:
            breaker_label = f"open ({breaker['open_for_secs']}s remaining)" if breaker["open"] else "closed"
        source = "env" if name in env_only else "registry"
        conn = connections.get(name)
        if conn is None:
            # Distinguish "unknown name" from "known but invalid config" so the
            # operator sees the config problem, not a doomed handshake.
            raw = registry.get(name)
            detail = "not resolvable"
            if raw is not None:
                problems = validate_connection_dict(name, raw)
                if problems:
                    detail = "; ".join(problems)
            return McpServerStatus(name=name, status="error", error=detail, source=source, breaker=breaker_label)
        try:
            async with asyncio.timeout(_PROBE_TIMEOUT_SECS):
                tools = await _load_server_tools(name, conn, _PROBE_TIMEOUT_SECS, None)
            return McpServerStatus(
                name=name, status="ok", tools=[t.name for t in tools], source=source, breaker=breaker_label
            )
        except TimeoutError:
            return McpServerStatus(
                name=name, status="error", error="probe timed out", source=source, breaker=breaker_label
            )
        except Exception as e:
            return McpServerStatus(name=name, status="error", error=str(e)[:300], source=source, breaker=breaker_label)

    return list(await asyncio.gather(*(_probe(n) for n in names)))
