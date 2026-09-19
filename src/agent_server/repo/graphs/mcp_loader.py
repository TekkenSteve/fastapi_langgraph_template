"""MCP tools for graphs: registry resolution, loading, and the wrapper.

Model: `langgraph.json` declares which MCP servers exist (top-level
``mcp_servers`` registry); a graph composes them in one line:

    from agent_server.contracts import with_mcp_tools
    graph = with_mcp_tools(build_my_agent, servers=["acme-kb"])

``with_mcp_tools`` returns a plain async factory — the framework loads
it like any other factory and needs zero MCP knowledge.

Trust tiers (docs/design/hub.md), highest precedence first:
- user: connections from the injected ``connection_provider`` — applied
  when ``user_scoped=True`` and the run carries a ``user_id``; only names
  the graph declared are resolved, so users can never inject a server the
  graph did not opt into. The provider is application-layer code (the hub
  package); the framework only knows its signature.
- ops override: ``MCP_SERVER__<NAME>`` env var (JSON connection object).
- registry: ``langgraph.json`` ``mcp_servers``.

Tool names are namespaced by server name, ``python`` stdio commands
normalize to the current interpreter, and failures degrade to zero
tools — MCP is additive and must never break graph startup.
"""

import asyncio
import inspect
import json
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import structlog
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from agent_server.config.graph_config import load_mcp_servers_config
from agent_server.config.settings import settings
from agent_server.infra.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    get_breaker_storage,
    reset_breaker_state,
)
from agent_server.repo.graphs.mcp_apps import ensure_mcp_apps_capability_advertised, filter_model_facing_tools

logger = structlog.getLogger(__name__)

# Looks up the caller's user-tier MCP connections (langchain-mcp-adapters
# connection map). Implemented by the application layer (the hub package) and
# injected by the graph — the framework only knows the signature.
ConnectionProvider = Callable[[str], Awaitable[dict[str, Any]]]


# Injected from the app layer (main.py): builds the per-invocation authz
# interceptor. repo must not import auth, so wiring lives at the composition root.
_tool_interceptor_factory: Callable[[str | None], Any] | None = None


def configure_tool_interceptor_factory(factory: Callable[[str | None], Any]) -> None:
    """Install the tool-call interceptor factory (app layer, at lifespan)."""
    global _tool_interceptor_factory
    _tool_interceptor_factory = factory


def _normalize_command(connections: dict[str, Any]) -> dict[str, Any]:
    """``python`` as a stdio command resolves to the current interpreter —
    correct in dev venvs and containers alike (production images have no .venv)."""
    for conn in connections.values():
        if isinstance(conn, dict) and conn.get("command") == "python":
            conn["command"] = sys.executable
    return connections


# Server names are lowercase letters, digits and hyphens — the same charset
# the hub enforces for user connections. Underscores are deliberately excluded
# because the env-override mapping (name → MCP_SERVER__NAME) is lossy for them
# ("acme_kb" and "acme-kb" would collide on the same variable).
SERVER_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")

# Required keys per transport, matching langchain-mcp-adapters' connection schema.
_TRANSPORT_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "stdio": ("command",),
    "sse": ("url",),
    "streamable_http": ("url",),
    "websocket": ("url",),
}


def validate_connection_dict(name: str, conn: dict[str, Any]) -> list[str]:
    """Light validation shared by every tier that produces a connection dict.

    Returns a list of human-readable problems (empty = valid). Callers decide
    what to do: the resolver warns and falls back/skips, the debug probe
    surfaces them as the error reason.
    """
    problems: list[str] = []
    if not SERVER_NAME_PATTERN.match(name):
        problems.append(f"server name {name!r} must be lowercase letters, digits, hyphens (no underscores)")
    if not isinstance(conn, dict):
        return [f"connection for {name!r} is not an object"]
    transport = conn.get("transport")
    if not transport:
        problems.append(f"{name!r} is missing 'transport'")
        return problems
    required = _TRANSPORT_REQUIRED_KEYS.get(transport)
    if required is None:
        problems.append(
            f"{name!r} has unknown transport {transport!r} (expected one of {sorted(_TRANSPORT_REQUIRED_KEYS)})"
        )
        return problems
    for key in required:
        if not conn.get(key):
            problems.append(f"{name!r} ({transport}) is missing required key {key!r}")
    url = conn.get("url")
    if transport != "stdio" and isinstance(url, str):
        scheme = urlparse(url).scheme
        if scheme not in ("http", "https", "ws", "wss"):
            problems.append(f"{name!r} url must be an absolute http(s)/ws(s) URL")
    return problems


def _resolve_registry_entry(name: str) -> dict[str, Any] | None:
    """Env override first, then the langgraph.json registry. None if unknown
    or invalid (config errors degrade to a loud warning, never a startup failure)."""
    raw = os.environ.get(f"MCP_SERVER__{name.upper().replace('-', '_')}", "").strip()
    if raw:
        try:
            conn = json.loads(raw)
            if isinstance(conn, dict):
                problems = validate_connection_dict(name, conn)
                if not problems:
                    return conn
                # An explicit but broken override must not silently mask the
                # registry — warn loudly, then fall through to the registry.
                logger.warning("mcp_env_invalid_connection", server=name, problems=problems)
            else:
                logger.warning("mcp_env_not_an_object", server=name)
        except json.JSONDecodeError as e:
            logger.warning("mcp_env_invalid_json", server=name, error=str(e))
    registry = load_mcp_servers_config()
    if name not in registry:
        return None
    conn = registry[name]
    problems = validate_connection_dict(name, conn)
    if problems:
        logger.warning("mcp_registry_invalid_connection", server=name, problems=problems)
        return None
    return conn


def resolve_mcp_connections(server_names: list[str]) -> dict[str, Any]:
    """Resolve server names against the deployment tiers (env + registry).

    Unknown names are skipped with a warning. For run-scoped resolution
    including user-tier connections, use :func:`aresolve_mcp_connections`.
    """
    connections: dict[str, Any] = {}
    for name in server_names:
        conn = _resolve_registry_entry(name)
        if conn is None:
            logger.warning("mcp_server_not_in_registry", server=name)
            continue
        connections[name] = conn
    return _normalize_command(connections)


async def aresolve_mcp_connections(
    server_names: list[str],
    *,
    user_id: str | None = None,
    connection_provider: ConnectionProvider | None = None,
) -> dict[str, Any]:
    """Resolve server names across all three trust tiers.

    User tier (from ``connection_provider``) wins on name match; env override
    beats the registry; unknown names degrade to a warning. Only declared
    names are resolved — a user cannot add a server the graph did not opt
    into. Without a provider there is no user tier (deployment tiers only).
    """
    user_connections: dict[str, Any] = {}
    if user_id and connection_provider is not None:
        try:
            user_connections = await connection_provider(user_id)
        except Exception as e:  # user-tier outage degrades to deployment tiers, never breaks a run
            logger.warning("mcp_user_connections_load_failed", user_id=user_id, error=str(e))

    connections: dict[str, Any] = {}
    for name in server_names:
        if name in user_connections:
            connections[name] = user_connections[name]
            continue
        conn = _resolve_registry_entry(name)
        if conn is None:
            logger.warning("mcp_server_not_in_registry", server=name)
            continue
        connections[name] = conn
    return _normalize_command(connections)


# --- per-server circuit breaker ----------------------------------------------
# A flapping MCP server should fast-fail, not pay a full handshake timeout on
# every graph load. The state machine is vendored in infra/circuit_breaker.py
# (see its docstring for why not pybreaker/aiobreaker). Storage mirrors the
# token store / SSE broker pattern: process memory by default, shared Redis
# (async client) when the broker is enabled, so one pod's failures protect
# the others.
_breakers: dict[str, Any] = {}


async def get_mcp_breaker_states() -> dict[str, dict[str, Any]]:
    """Read-only breaker view for the debug probe. Only servers loaded at
    least once this process appear — never-touched servers have no state."""
    return {name: await breaker.state_snapshot() for name, breaker in _breakers.items()}


def env_override_names() -> list[str]:
    """Server names supplied purely via MCP_SERVER__* env (not in the registry)."""
    names = []
    for var in os.environ:
        if var.startswith("MCP_SERVER__"):
            names.append(var[len("MCP_SERVER__") :].lower().replace("_", "-"))
    return names


def _get_breaker(name: str) -> Any:
    """The CircuitBreaker for one MCP server (created lazily)."""
    if name not in _breakers:
        redis_client = None
        if settings.redis.REDIS_BROKER_ENABLED:
            from agent_server.infra.redis import redis_manager

            redis_client = redis_manager.get_client()
        _breakers[name] = CircuitBreaker(
            f"mcp:{name}",
            fail_max=settings.mcp.MCP_BREAKER_THRESHOLD,
            cooldown_secs=settings.mcp.MCP_BREAKER_COOLDOWN_SECS,
            storage=get_breaker_storage(redis_client=redis_client),
        )
    return _breakers[name]


def reset_mcp_breakers() -> None:
    """Drop all breaker state (tests; not needed at runtime)."""
    _breakers.clear()
    reset_breaker_state()


# --- stringified-JSON args fixer ----------------------------------------------
# Some models (notably Gemini) serialize nested objects as JSON strings when
# calling tools with complex input schemas. Parse those args when the schema
# expects object/array (or is untyped); string-typed args are never touched.


def _fix_stringified_json_args(schema_props: dict[str, Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Parse stringified-JSON kwargs per the tool's args schema."""
    if not schema_props:
        return kwargs
    fixed = dict(kwargs)
    for key, value in fixed.items():
        if not isinstance(value, str):
            continue
        expected = schema_props.get(key, {}).get("type", "")
        if expected == "string" or (isinstance(expected, list) and "string" in expected):
            continue
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, (dict, list)):
            fixed[key] = parsed
    return fixed


class _JsonArgsFixedTool(BaseTool):
    """Delegation wrapper: repairs stringified-JSON args, then invokes inner."""

    inner: Any = None

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("MCP tools are async-only")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        fixed = _fix_stringified_json_args(self.args or {}, kwargs)
        return await self.inner.ainvoke(fixed)


def _wrap_with_args_fixer(tool: BaseTool) -> BaseTool:
    """Wrap one tool with the stringified-JSON args repair."""
    return _JsonArgsFixedTool(
        inner=tool,
        name=tool.name,
        description=tool.description,
        args_schema=getattr(tool, "args_schema", None),
        metadata=getattr(tool, "metadata", None),
    )


async def _load_server_tools(name: str, conn: Any, timeout: float, interceptors: list | None) -> list[BaseTool]:
    """Handshake one MCP server and return its tools (raises on failure)."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({name: conn}, tool_interceptors=interceptors, tool_name_prefix=True)
    async with asyncio.timeout(timeout):
        return list(await client.get_tools())


async def load_mcp_tools(
    connections: dict[str, Any],
    *,
    interceptors: list | None = None,
    user_id: str | None = None,
) -> list[BaseTool]:
    # MCP Apps: advertise the ui capability and keep app-only tools out of the
    # model's list when enabled (see repo/graphs/mcp_apps.py).
    if settings.mcp.MCP_APPS_ENABLED:
        ensure_mcp_apps_capability_advertised()
    if settings.mcp.MCP_TOOL_AUTHZ_ENABLED and _tool_interceptor_factory is not None:
        # Per-invocation authorization (Level-4 maturity): every tool call goes
        # through the policy engine. Prepend so it runs outermost. The factory
        # is injected from the app layer (repo must not import auth).
        interceptors = [_tool_interceptor_factory(user_id), *(interceptors or [])]
    """Load tools from MCP servers given a connections map.

    Per-server fault isolation: servers load concurrently, and one server's
    failure costs only that server's tools (repeated failures trip its
    circuit breaker — see above). A hung server degrades after
    MCP_LOAD_TIMEOUT_SECS instead of stalling startup.

    Args:
        connections: langchain-mcp-adapters connection map.
        interceptors: optional ToolCallInterceptor chain (retry/cache/logging).

    Returns:
        Tools from all reachable servers, namespaced by server name.
    """
    if not connections:
        return []
    timeout = float(os.environ.get("MCP_LOAD_TIMEOUT_SECS", "15"))

    async def _guarded_load(name: str, conn: Any) -> list[BaseTool]:
        return await _get_breaker(name).call(_load_server_tools, name, conn, timeout, interceptors)

    pending = {name: _guarded_load(name, conn) for name, conn in connections.items()}
    results = await asyncio.gather(*pending.values(), return_exceptions=True)
    tools: list[BaseTool] = []
    for name, result in zip(pending, results, strict=True):
        if isinstance(result, CircuitOpenError):
            logger.info("mcp_breaker_open_skipped", server=name)
            continue
        if isinstance(result, TimeoutError):
            logger.warning("mcp_tools_load_timeout", server=name)
            continue
        if isinstance(result, BaseException):
            logger.warning("mcp_tools_load_failed", server=name, error=str(result))
            continue
        tools.extend(_wrap_with_args_fixer(t) for t in result)
    if settings.mcp.MCP_APPS_ENABLED:
        tools = filter_model_facing_tools(tools)
    logger.info("mcp_tools_loaded", servers=list(pending), tools=[t.name for t in tools])
    return tools


# Per-process TTL cache for user-scoped tool loads. A per-request factory
# would otherwise pay an MCP handshake per run. TTL bounds staleness after
# a connection edit — including across instances (no cross-pod invalidation).
_user_tools_cache: dict[tuple[str, tuple[str, ...]], tuple[float, list[BaseTool]]] = {}


async def _load_user_scoped_tools(
    user_id: str | None, servers: list[str], connection_provider: ConnectionProvider
) -> list[BaseTool]:
    """Resolve + load MCP tools for one run, cached per (user, servers)."""
    key = (user_id or "", tuple(sorted(servers)))
    now = time.monotonic()
    hit = _user_tools_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    connections = await aresolve_mcp_connections(
        list(servers), user_id=user_id, connection_provider=connection_provider
    )
    tools = await load_mcp_tools(connections)
    _user_tools_cache[key] = (now + settings.mcp.MCP_USER_TOOLS_CACHE_TTL_SECS, tools)
    return tools


def clear_user_tools_cache() -> None:
    """Drop every cached user-scoped tool list (tests; not needed at runtime)."""
    _user_tools_cache.clear()


def with_mcp_tools(
    build: Callable[..., Any],
    servers: list[str],
    *,
    tools_param: str = "mcp_tools",
    user_scoped: bool = False,
    connection_provider: ConnectionProvider | None = None,
) -> Callable[..., Awaitable[Any]]:
    """Wrap a graph builder: resolve MCP tools from the registry, then build.

    Returns an async factory — the framework loads it like any other graph
    factory. The builder receives the tools under ``tools_param`` (empty list
    when nothing resolved).

    With ``user_scoped=True`` the factory takes the run ``config`` — the
    framework classifies it as a per-request factory, and each run resolves
    the caller's user-tier connections (from ``configurable.user_id``, via the
    injected ``connection_provider``) on top of the deployment tiers. Tool
    loads are TTL-cached per (user, servers); the graph build itself still
    runs per request, as with any factory graph.
    """
    if not user_scoped:

        async def factory() -> Any:
            tools = await load_mcp_tools(resolve_mcp_connections(servers))
            result = build(**{tools_param: tools})
            # generate_graph dispatches the factory's return type only once — an
            # async builder's coroutine must be awaited here to not leak through.
            if inspect.isawaitable(result):
                return await result
            return result

        return factory

    if connection_provider is None:
        # Fail at graph-authoring time, not silently per run: opting into the
        # user tier without a provider would resolve deployment tiers only.
        raise ValueError("with_mcp_tools(user_scoped=True) requires connection_provider")
    provider: ConnectionProvider = connection_provider

    async def user_factory(config: RunnableConfig) -> Any:
        user_id = (config or {}).get("configurable", {}).get("user_id")
        tools = await _load_user_scoped_tools(user_id, servers, provider)
        result = build(**{tools_param: tools})
        if inspect.isawaitable(result):
            return await result
        return result

    return user_factory
