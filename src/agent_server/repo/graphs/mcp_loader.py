"""MCP tools for graphs: registry resolution, loading, and the wrapper.

Model: `langgraph.json` declares which MCP servers exist (top-level
``mcp_servers`` registry); a graph composes them in one line:

    from agent_server.contracts import with_mcp_tools
    graph = with_mcp_tools(build_my_agent, servers=["acme-kb"])

``with_mcp_tools`` returns a plain async factory — the framework loads
it like any other factory and needs zero MCP knowledge.

Trust tiers, highest precedence first:
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
import hashlib
import inspect
import json
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import structlog
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from agent_server.config.graph_config import load_mcp_servers_config
from agent_server.config.settings import settings
from agent_server.domain.run_config import configurable_user_id
from agent_server.infra.circuit_breaker import (
    CircuitBreaker,
    get_breaker_storage,
    reset_breaker_state,
)
from agent_server.repo.graphs.mcp_apps import ensure_mcp_apps_capability_advertised, filter_model_facing_tools
from agent_server.repo.graphs.mcp_errors import classify_mcp_error

logger = structlog.getLogger(__name__)

# The resolved form of one server: how to connect, plus what may be used from
# it. A connection dict alone cannot carry the allowlist — it is handed to
# langchain-mcp-adapters, which validates its shape — so the two travel together.
ALLOWED_TOOLS_KEY = "allowed_tools"


@dataclass(frozen=True)
class McpConnectionSpec:
    """One resolved server endpoint and its tool allowlist."""

    connection: dict[str, Any]
    allowed_tools: tuple[str, ...] = ()

    @classmethod
    def of(cls, connection: dict[str, Any], allowed_tools: Sequence[str] | None = None) -> "McpConnectionSpec":
        """Build a spec, lifting ``allowed_tools`` out of the connection object.

        Every tier may narrow a server (the hub column, the langgraph.json
        registry entry, the env override); the key is removed from the dict the
        adapter sees, because its schema does not know that field.
        """
        spec = dict(connection)
        declared = spec.pop(ALLOWED_TOOLS_KEY, None)
        names = tuple(allowed_tools if allowed_tools is not None else (declared or ()))
        return cls(connection=spec, allowed_tools=names)


# Looks up the caller's user-tier MCP connections, keyed by server name. A
# ``None`` value is an explicit block (the caller disabled that name).
# Implemented by the application layer (the hub package) and injected by the
# graph — the framework only knows the signature.
ConnectionProvider = Callable[[str], Awaitable[dict[str, McpConnectionSpec | None]]]


# Injected from the app layer (main.py): builds the per-invocation authz
# interceptor. repo must not import auth, so wiring lives at the composition root.
_tool_interceptor_factory: Callable[[str | None], Any] | None = None


def configure_tool_interceptor_factory(factory: Callable[[str | None], Any]) -> None:
    """Install the tool-call interceptor factory (app layer, at lifespan)."""
    global _tool_interceptor_factory
    _tool_interceptor_factory = factory


def _normalize_command(connections: dict[str, McpConnectionSpec]) -> dict[str, McpConnectionSpec]:
    """``python`` as a stdio command resolves to the current interpreter —
    correct in dev venvs and containers alike (production images have no .venv)."""
    for spec in connections.values():
        if spec.connection.get("command") == "python":
            spec.connection["command"] = sys.executable
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


def _resolve_registry_entry(name: str) -> McpConnectionSpec | None:
    """Env override first, then the langgraph.json registry. None if unknown
    or invalid (config errors degrade to a loud warning, never a startup failure)."""
    raw = os.environ.get(f"MCP_SERVER__{name.upper().replace('-', '_')}", "").strip()
    if raw:
        try:
            conn = json.loads(raw)
            if isinstance(conn, dict):
                problems = validate_connection_dict(name, conn)
                if not problems:
                    return McpConnectionSpec.of(conn)
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
    return McpConnectionSpec.of(conn)


def resolve_mcp_connections(server_names: list[str]) -> dict[str, McpConnectionSpec]:
    """Resolve server names against the deployment tiers (env + registry).

    Unknown names are skipped with a warning. For run-scoped resolution
    including user-tier connections, use :func:`aresolve_mcp_connections`.
    """
    connections: dict[str, McpConnectionSpec] = {}
    for name in server_names:
        spec = _resolve_registry_entry(name)
        if spec is None:
            logger.warning("mcp_server_not_in_registry", server=name)
            continue
        connections[name] = spec
    return _normalize_command(connections)


async def aresolve_mcp_connections(
    server_names: list[str],
    *,
    user_id: str | None = None,
    connection_provider: ConnectionProvider | None = None,
) -> dict[str, McpConnectionSpec]:
    """Resolve server names across all three trust tiers.

    User tier (from ``connection_provider``) wins on name match; env override
    beats the registry; unknown names degrade to a warning. Only declared
    names are resolved — a user cannot add a server the graph did not opt
    into. Without a provider there is no user tier (deployment tiers only).

    A ``None`` value in the provider map is an **explicit block**: the caller
    disabled that connection, so the name resolves to nothing rather than
    silently falling back to a deployment server of the same name.
    """
    user_connections: dict[str, McpConnectionSpec | None] = {}
    if user_id and connection_provider is not None:
        try:
            user_connections = await connection_provider(user_id)
        except Exception as e:  # user-tier outage degrades to deployment tiers, never breaks a run
            logger.warning("mcp_user_connections_load_failed", user_id=user_id, error=str(e))

    connections: dict[str, McpConnectionSpec] = {}
    for name in server_names:
        if name in user_connections:
            spec = user_connections[name]
            if spec is None:
                logger.info("mcp_user_connection_disabled", server=name)
                continue
            connections[name] = spec
            continue
        spec = _resolve_registry_entry(name)
        if spec is None:
            logger.warning("mcp_server_not_in_registry", server=name)
            continue
        connections[name] = spec
    return _normalize_command(connections)


# --- per-server circuit breaker ----------------------------------------------
# A flapping MCP server should fast-fail, not pay a full handshake timeout on
# every graph load. The state machine is vendored in infra/circuit_breaker.py
# (see its docstring for why not pybreaker/aiobreaker). Storage mirrors the
# token store / SSE broker pattern: process memory by default, shared Redis
# (async client) when the broker is enabled, so one pod's failures protect
# the others.
#
# Keyed by server name **and endpoint fingerprint**: two tenants can both name
# a connection "acme-kb" while pointing at different endpoints, and one
# tenant's flapping server must not trip the other's. Identical specs share
# one breaker, which is right — they are the same server.
_breakers: dict[str, Any] = {}
_breaker_server_names: dict[str, str] = {}


def breaker_key(name: str, conn: dict[str, Any]) -> str:
    """Registry key for one server endpoint (name + spec fingerprint).

    The allowlist is deliberately not part of it: the same endpoint failing is
    the same endpoint failing, whoever narrowed its tools.
    """
    return f"mcp:{name}:{_fingerprint_connections({name: McpConnectionSpec.of(conn)})}"


async def get_mcp_breaker_states() -> dict[str, dict[str, Any]]:
    """Read-only breaker view for the debug probe, keyed by server name.

    Only servers loaded at least once this process appear — never-touched
    servers have no state. Several user-tier endpoints can share a name; the
    probe reports the unhealthy one, since that is what the operator needs to
    see.
    """
    states: dict[str, dict[str, Any]] = {}
    for key, breaker in _breakers.items():
        name = _breaker_server_names.get(key, key)
        snapshot = await breaker.state_snapshot()
        current = states.get(name)
        if current is None or (snapshot["open"] and not current["open"]):
            states[name] = snapshot
    return states


def env_override_names() -> list[str]:
    """Server names supplied purely via MCP_SERVER__* env (not in the registry)."""
    names = []
    for var in os.environ:
        if var.startswith("MCP_SERVER__"):
            names.append(var[len("MCP_SERVER__") :].lower().replace("_", "-"))
    return names


def mcp_breaker(name: str, conn: dict[str, Any]) -> Any:
    """The CircuitBreaker for one server *endpoint* (created lazily).

    Public because the hub's MCP Apps host proxy runs the same endpoint and
    must share its failure state (see ``breaker_key``).
    """
    key = breaker_key(name, conn)
    if key not in _breakers:
        redis_client = None
        if settings.redis.REDIS_BROKER_ENABLED:
            from agent_server.infra.redis import redis_manager

            redis_client = redis_manager.get_client()
        _breakers[key] = CircuitBreaker(
            key,
            fail_max=settings.mcp.MCP_BREAKER_THRESHOLD,
            cooldown_secs=settings.mcp.MCP_BREAKER_COOLDOWN_SECS,
            storage=get_breaker_storage(redis_client=redis_client),
        )
        _breaker_server_names[key] = name
    return _breakers[key]


def reset_mcp_breakers() -> None:
    """Drop all breaker state (tests; not needed at runtime)."""
    _breakers.clear()
    _breaker_server_names.clear()
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


# --- LLM-safe tool names -------------------------------------------------------
# MCP servers name their tools freely, but strict function-calling APIs accept
# only ``^[a-zA-Z0-9_-]{1,64}$`` — and an illegal name fails the whole request,
# not just that tool. Names are normalized at the model-facing boundary; the
# wrapper still routes to the original tool, and the original name is kept in
# metadata for logs/UI.
_LLM_SAFE_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_ILLEGAL_TOOL_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")
_MAX_TOOL_NAME_LEN = 64


def tool_allowed(tool_name: str, allowed: Sequence[str] | None) -> bool:
    """Whether *tool_name* survives an allowlist.

    Empty/None means no restriction. An entry matches the exposed name or its
    suffix after the server prefix (upstreams are created with
    ``tool_name_prefix=True``, so a user writes either ``server_tool`` or
    ``tool`` — refusing the short form would be surprising, not safer).
    """
    if not allowed:
        return True
    return any(tool_name == entry or tool_name.endswith(f"_{entry}") for entry in allowed)


def sanitize_tool_name(name: str, *, used: set[str] | None = None) -> str:
    """Return a function-calling-safe tool name, unique within *used*.

    Illegal runs collapse to ``_``, over-long names are truncated (leaving room
    for a collision suffix), and collisions get ``_2``/``_3``. Passing the same
    *used* set across one catalog is what makes the result stable and unique;
    callers that need to map a name back must replay it in the same order.
    """
    base = name if _LLM_SAFE_TOOL_NAME.match(name) else _ILLEGAL_TOOL_NAME_CHARS.sub("_", name).strip("_")
    base = base or "tool"
    if len(base) > _MAX_TOOL_NAME_LEN:
        base = base[: _MAX_TOOL_NAME_LEN - 4].rstrip("_") or "tool"
    candidate = base
    suffix = 2
    while used is not None and candidate in used:
        extra = f"_{suffix}"
        stem = base[: _MAX_TOOL_NAME_LEN - len(extra)].rstrip("_") or "tool"
        candidate = f"{stem}{extra}"
        suffix += 1
    if used is not None:
        used.add(candidate)
    return candidate


def _wrap_with_args_fixer(tool: BaseTool, *, exposed_name: str) -> BaseTool:
    """Wrap one tool: stringified-JSON args repair plus its exposed name."""
    metadata = dict(getattr(tool, "metadata", None) or {})
    if exposed_name != tool.name:
        metadata["original_tool_name"] = tool.name
    return _JsonArgsFixedTool(
        inner=tool,
        name=exposed_name,
        description=tool.description,
        args_schema=getattr(tool, "args_schema", None),
        metadata=metadata or None,
    )


async def _load_server_tools(name: str, conn: Any, timeout: float, interceptors: list | None) -> list[BaseTool]:
    """Handshake one MCP server and return its tools (raises on failure)."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({name: conn}, tool_interceptors=interceptors, tool_name_prefix=True)
    async with asyncio.timeout(timeout):
        return list(await client.get_tools())


async def load_mcp_tools(
    connections: dict[str, McpConnectionSpec],
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
    timeout = settings.mcp.MCP_LOAD_TIMEOUT_SECS

    async def _guarded_load(name: str, conn: dict[str, Any]) -> list[BaseTool]:
        return await mcp_breaker(name, conn).call(_load_server_tools, name, conn, timeout, interceptors)

    pending = {name: _guarded_load(name, spec.connection) for name, spec in connections.items()}
    results = await asyncio.gather(*pending.values(), return_exceptions=True)
    tools: list[BaseTool] = []
    for name, result in zip(pending, results, strict=True):
        if isinstance(result, BaseException):
            # Same classification the API surfaces use, so "auth" and "down"
            # are distinguishable in logs too.
            failure = classify_mcp_error(result, server=name)
            logger.warning("mcp_tools_load_failed", server=name, reason=failure.reason.value, error=failure.message)
            continue
        allowed = connections[name].allowed_tools
        if allowed:
            kept = [tool for tool in result if tool_allowed(tool.name, allowed)]
            if len(kept) != len(result):
                logger.info("mcp_tools_allowlisted", server=name, kept=len(kept), loaded=len(result))
            result = kept
        tools.extend(result)
    if settings.mcp.MCP_APPS_ENABLED:
        tools = filter_model_facing_tools(tools)
    # One `used` set across the whole catalog: the model sees a flat list, so
    # uniqueness has to hold there, not per server.
    used_names: set[str] = set()
    exposed = [
        _wrap_with_args_fixer(tool, exposed_name=sanitize_tool_name(tool.name, used=used_names)) for tool in tools
    ]
    logger.info("mcp_tools_loaded", servers=list(pending), tools=[t.name for t in exposed])
    return exposed


# Process-local cache for MCP tool loads, keyed by (scope, spec fingerprint).
#
# The fingerprint covers every field that decides a live session, so editing a
# URL/header/command/env takes effect on the next run instead of waiting for the
# TTL. The TTL still bounds staleness for changes the spec cannot show (a
# provider row edited underneath an identical spec) and across pods — there is
# no cross-pod invalidation.
_FINGERPRINT_KEYS = ("transport", "url", "headers", "command", "args", "env")

_tools_cache: dict[tuple[str, str], tuple[float, list[BaseTool]]] = {}


def _fingerprint_connections(connections: dict[str, McpConnectionSpec]) -> str:
    """Stable hash of what decides the loaded tool list.

    The allowlist belongs in here: two callers with the same endpoint but
    different allowlists must not share a cache entry.
    """
    payload = {
        name: {
            **{key: spec.connection.get(key) for key in _FINGERPRINT_KEYS if key in spec.connection},
            ALLOWED_TOOLS_KEY: list(spec.allowed_tools),
        }
        for name, spec in sorted(connections.items())
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _cache_scope(user_id: str | None) -> str:
    """Cache scope for one load.

    Tools carry user-bound interceptors when per-invocation authorization is on,
    so in that mode they must not be shared across users.
    """
    if settings.mcp.MCP_TOOL_AUTHZ_ENABLED:
        return f"authz:{user_id or 'system'}"
    return "shared"


async def _cached_load_tools(
    connections: dict[str, McpConnectionSpec], *, user_id: str | None = None
) -> list[BaseTool]:
    """Load tools for *connections*, served from the (scope, fingerprint) cache."""
    if not connections:
        return []
    key = (_cache_scope(user_id), _fingerprint_connections(connections))
    now = time.monotonic()
    hit = _tools_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    tools = await load_mcp_tools(connections, user_id=user_id)
    for expired in [k for k, (expires, _) in _tools_cache.items() if expires <= now]:
        _tools_cache.pop(expired, None)
    _tools_cache[key] = (now + settings.mcp.MCP_USER_TOOLS_CACHE_TTL_SECS, tools)
    return tools


def clear_mcp_tools_cache() -> None:
    """Drop every cached MCP tool list (tests; not needed at runtime)."""
    _tools_cache.clear()


async def _build_with_tools(build: Callable[..., Any], tools_param: str, tools: list[BaseTool]) -> Any:
    """Invoke the builder with the resolved tools, awaiting an async builder.

    ``generate_graph`` dispatches the factory's return type only once — an async
    builder's coroutine must be awaited here to not leak through.
    """
    result = build(**{tools_param: tools})
    if inspect.isawaitable(result):
        return await result
    return result


def with_mcp_tools(
    build: Callable[..., Any],
    servers: list[str],
    *,
    tools_param: str = "mcp_tools",
    per_run: bool = False,
    user_scoped: bool = False,
    connection_provider: ConnectionProvider | None = None,
) -> Callable[..., Awaitable[Any]]:
    """Wrap a graph builder: resolve MCP tools from the registry, then build.

    Returns an async factory — the framework loads it like any other graph
    factory. The builder receives the tools under ``tools_param`` (empty list
    when nothing resolved).

    ``per_run=True`` makes the factory take the run ``config``, so the framework
    classifies it as a per-request factory and the graph — including any sandbox
    its backend owns — is built fresh per run. Deployment-tier tool loads are
    served from the fingerprint cache, so this does not re-handshake MCP per run.

    ``user_scoped=True`` implies per-run and additionally resolves the caller's
    user-tier connections (via the injected ``connection_provider``) on top of
    the deployment tiers. Only names the graph declared are resolved, so a user
    can never inject a server the graph did not opt into.
    """
    if user_scoped:
        if connection_provider is None:
            # Fail at graph-authoring time, not silently per run: opting into the
            # user tier without a provider would resolve deployment tiers only.
            raise ValueError("with_mcp_tools(user_scoped=True) requires connection_provider")
        provider: ConnectionProvider = connection_provider

        async def user_factory(config: RunnableConfig) -> Any:
            user_id = configurable_user_id(config)
            connections = await aresolve_mcp_connections(list(servers), user_id=user_id, connection_provider=provider)
            tools = await _cached_load_tools(connections, user_id=user_id)
            return await _build_with_tools(build, tools_param, tools)

        return user_factory

    if per_run:

        async def per_run_factory(config: RunnableConfig) -> Any:
            user_id = configurable_user_id(config)
            tools = await _cached_load_tools(resolve_mcp_connections(servers), user_id=user_id)
            return await _build_with_tools(build, tools_param, tools)

        return per_run_factory

    async def factory() -> Any:
        tools = await _cached_load_tools(resolve_mcp_connections(servers))
        return await _build_with_tools(build, tools_param, tools)

    return factory
