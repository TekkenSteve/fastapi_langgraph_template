"""MCP loader hub tests: user-tier resolution via injected providers."""

from typing import Any

import pytest

import agent_server.repo.graphs.mcp_loader as loader
from agent_server.repo.graphs.mcp_loader import (
    aresolve_mcp_connections,
    clear_user_tools_cache,
    with_mcp_tools,
)

_REGISTRY: dict[str, Any] = {
    "acme-kb": {"transport": "stdio", "command": "python", "args": ["srv.py"]},
}

_USER_CONNECTIONS: dict[str, Any] = {
    "acme-kb": {
        "transport": "streamable_http",
        "url": "https://user.example.com/kb",
        "headers": {"Authorization": "t"},
    },
    "other-kb": {"transport": "streamable_http", "url": "https://user.example.com/other"},
}


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "load_mcp_servers_config", lambda: dict(_REGISTRY))
    clear_user_tools_cache()


def _provider(mapping: dict[str, Any]) -> loader.ConnectionProvider:
    async def _load(user_id: str) -> dict[str, Any]:
        return dict(mapping)

    return _load


async def test_user_connection_overrides_registry_entry() -> None:
    resolved = await aresolve_mcp_connections(
        ["acme-kb"], user_id="u1", connection_provider=_provider(_USER_CONNECTIONS)
    )
    assert resolved["acme-kb"] == _USER_CONNECTIONS["acme-kb"]


async def test_undeclared_user_connection_is_never_injected() -> None:
    """The user has an "other-kb" connection, but the graph only opted into
    "acme-kb" — the user tier can never add a server the graph did not declare."""
    resolved = await aresolve_mcp_connections(
        ["acme-kb"], user_id="u1", connection_provider=_provider(_USER_CONNECTIONS)
    )
    assert "other-kb" not in resolved


async def test_user_connection_supplies_name_absent_from_registry() -> None:
    resolved = await aresolve_mcp_connections(
        ["other-kb"], user_id="u1", connection_provider=_provider(_USER_CONNECTIONS)
    )
    assert resolved["other-kb"] == _USER_CONNECTIONS["other-kb"]


async def test_no_provider_means_no_user_tier() -> None:
    """Even with a user_id, without a provider resolution stays on the
    deployment tiers — the framework has no user-tier knowledge of its own."""
    resolved = await aresolve_mcp_connections(["acme-kb"], user_id="u1")
    assert resolved["acme-kb"]["transport"] == "stdio"


async def test_provider_failure_degrades_to_registry() -> None:
    async def _boom(user_id: str) -> dict[str, Any]:
        raise ConnectionError("db down")

    resolved = await aresolve_mcp_connections(["acme-kb"], user_id="u1", connection_provider=_boom)
    assert resolved["acme-kb"]["transport"] == "stdio"


async def test_env_override_loses_to_user_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_SERVER__ACME_KB", '{"transport": "stdio", "command": "true"}')
    resolved = await aresolve_mcp_connections(
        ["acme-kb"], user_id="u1", connection_provider=_provider(_USER_CONNECTIONS)
    )
    assert resolved["acme-kb"] == _USER_CONNECTIONS["acme-kb"]


async def test_with_mcp_tools_default_wraps_as_zero_arg_factory() -> None:
    factory = with_mcp_tools(lambda mcp_tools: "built", servers=["acme-kb"])
    assert len(__import__("inspect").signature(factory).parameters) == 0


def test_user_scoped_without_provider_fails_at_authoring_time() -> None:
    """Opting into the user tier without a provider would silently resolve
    deployment tiers only — reject it where the graph is defined."""
    with pytest.raises(ValueError, match="connection_provider"):
        with_mcp_tools(lambda mcp_tools: "built", servers=["acme-kb"], user_scoped=True)


async def test_user_scoped_factory_takes_config_for_per_request_dispatch() -> None:
    """The config parameter is what makes classify_factory treat the graph as
    a per-request factory — losing it would silently pin user resolution."""
    import inspect

    factory = with_mcp_tools(
        lambda mcp_tools: "built", servers=["acme-kb"], user_scoped=True, connection_provider=_provider({})
    )
    assert list(inspect.signature(factory).parameters) == ["config"]


async def test_user_scoped_loads_are_cached_per_user_and_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []

    async def _fake_resolve(
        names: list[str], *, user_id: str | None = None, connection_provider: Any = None
    ) -> dict[str, Any]:
        calls.append(user_id)
        return {}

    async def _fake_load(connections: dict[str, Any], *, interceptors: Any = None) -> list:
        return ["tool"]

    monkeypatch.setattr(loader, "aresolve_mcp_connections", _fake_resolve)
    monkeypatch.setattr(loader, "load_mcp_tools", _fake_load)

    built = with_mcp_tools(
        lambda mcp_tools: mcp_tools, servers=["acme-kb"], user_scoped=True, connection_provider=_provider({})
    )
    config = {"configurable": {"user_id": "u1"}}

    assert await built(config) == ["tool"]
    assert await built(config) == ["tool"]
    assert calls == ["u1"]  # second call served from the TTL cache

    await built({"configurable": {"user_id": "u2"}})
    assert calls == ["u1", "u2"]  # cache key includes the user


async def test_user_scoped_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def _fake_resolve(
        names: list[str], *, user_id: str | None = None, connection_provider: Any = None
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {}

    monkeypatch.setattr(loader, "aresolve_mcp_connections", _fake_resolve)
    monkeypatch.setattr(loader.settings.mcp, "MCP_USER_TOOLS_CACHE_TTL_SECS", -1.0)

    built = with_mcp_tools(
        lambda mcp_tools: mcp_tools, servers=["acme-kb"], user_scoped=True, connection_provider=_provider({})
    )
    config = {"configurable": {"user_id": "u1"}}
    await built(config)
    await built(config)
    assert calls == 2


# --- circuit breaker + per-server isolation + args fixer ----------------------


@pytest.fixture(autouse=True)
def _breakers() -> None:
    loader.reset_mcp_breakers()


def _fake_tools(name: str) -> list:
    from langchain_core.tools import BaseTool

    class _T(BaseTool):
        name: str
        description: str

        def _run(self, **kwargs: Any) -> Any:
            raise NotImplementedError

        async def _arun(self, **kwargs: Any) -> Any:
            return kwargs

    return [_T(name=name, description=f"{name} tool")]


async def test_one_server_failure_costs_only_its_own_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_load(name: str, conn: dict, timeout: float, interceptors: Any) -> list:
        if name == "bad":
            raise ConnectionError("refused")
        return _fake_tools(name)

    monkeypatch.setattr(loader, "_load_server_tools", fake_load)
    tools = await loader.load_mcp_tools({"bad": {}, "good": {}})
    assert [t.name for t in tools] == ["good"]


async def test_breaker_opens_after_threshold_and_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_load(name: str, conn: dict, timeout: float, interceptors: Any) -> list:
        calls.append(name)
        raise ConnectionError("refused")

    monkeypatch.setattr(loader, "_load_server_tools", fake_load)
    monkeypatch.setattr(loader.settings.mcp, "MCP_BREAKER_THRESHOLD", 3)

    for _ in range(3):
        await loader.load_mcp_tools({"bad": {}})
    assert calls == ["bad", "bad", "bad"]

    await loader.load_mcp_tools({"bad": {}})
    assert calls == ["bad", "bad", "bad"]  # open — fast-failed, no attempt


async def test_breaker_resets_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"fail": True}

    async def fake_load(name: str, conn: dict, timeout: float, interceptors: Any) -> list:
        if state["fail"]:
            raise ConnectionError("refused")
        return _fake_tools(name)

    monkeypatch.setattr(loader, "_load_server_tools", fake_load)
    monkeypatch.setattr(loader.settings.mcp, "MCP_BREAKER_THRESHOLD", 2)
    monkeypatch.setattr(loader.settings.mcp, "MCP_BREAKER_COOLDOWN_SECS", -1.0)  # immediately half-open

    await loader.load_mcp_tools({"bad": {}})
    await loader.load_mcp_tools({"bad": {}})  # opens
    state["fail"] = False
    tools = await loader.load_mcp_tools({"bad": {}})  # half-open success
    assert [t.name for t in tools] == ["bad"]
    tools = await loader.load_mcp_tools({"bad": {}})
    assert [t.name for t in tools] == ["bad"]  # closed again


def test_args_fixer_parses_only_non_string_typed() -> None:
    schema = {
        "obj": {"type": "object"},
        "arr": {"type": "array"},
        "any": {},
        "s": {"type": "string"},
    }
    fixed = loader._fix_stringified_json_args(
        schema,
        {
            "obj": '{"x": 1}',
            "arr": "[1, 2]",
            "any": '{"y": 2}',
            "s": '{"keep": "as string"}',
        },
    )
    assert fixed["obj"] == {"x": 1}
    assert fixed["arr"] == [1, 2]
    assert fixed["any"] == {"y": 2}
    assert fixed["s"] == '{"keep": "as string"}'


def test_args_fixer_leaves_non_json_strings_alone() -> None:
    fixed = loader._fix_stringified_json_args({"obj": {"type": "object"}}, {"obj": "not json"})
    assert fixed["obj"] == "not json"


async def test_loaded_tools_are_wrapped_with_args_fixer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_load(name: str, conn: dict, timeout: float, interceptors: Any) -> list:
        return _fake_tools(name)

    monkeypatch.setattr(loader, "_load_server_tools", fake_load)
    tools = await loader.load_mcp_tools({"srv": {}})
    assert type(tools[0]).__name__ == "_JsonArgsFixedTool"
    assert tools[0].name == "srv"  # identity preserved through the wrapper


async def test_mcp_apps_enabled_filters_app_only_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ui_tool(name: str, visibility: list[str]) -> Any:
        from langchain_core.tools import BaseTool

        class _T(BaseTool):
            name: str
            description: str

            def _run(self, **kwargs: Any) -> Any:
                raise NotImplementedError

            async def _arun(self, **kwargs: Any) -> Any:
                return kwargs

        return _T(name=name, description=f"{name} t", metadata={"_meta": {"ui": {"visibility": visibility}}})

    async def fake_load(name: str, conn: dict, timeout: float, interceptors: Any) -> list:
        return [_ui_tool("model-tool", ["model", "app"]), _ui_tool("ui-only", ["app"])]

    monkeypatch.setattr(loader, "_load_server_tools", fake_load)
    monkeypatch.setattr(loader.settings.mcp, "MCP_APPS_ENABLED", True)
    tools = await loader.load_mcp_tools({"srv": {}})
    assert [t.name for t in tools] == ["model-tool"]


async def test_tool_authz_interceptor_prepended_with_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP_TOOL_AUTHZ_ENABLED wires per-invocation authorization, carrying the
    user identity from the user-scoped load path."""
    captured: dict[str, Any] = {}

    async def fake_load(name: str, conn: dict, timeout: float, interceptors: Any) -> list:
        captured["interceptors"] = interceptors
        return _fake_tools(name)

    monkeypatch.setattr(loader, "_load_server_tools", fake_load)
    monkeypatch.setattr(loader.settings.mcp, "MCP_TOOL_AUTHZ_ENABLED", True)

    # The factory is injected from the app layer at lifespan (composition
    # root) — simulate that wiring here.
    from agent_server.auth.policy import LocalPolicyEngine
    from agent_server.auth.tool_authz import PolicyToolInterceptor

    loader.configure_tool_interceptor_factory(lambda uid: PolicyToolInterceptor(LocalPolicyEngine(), uid))
    try:
        await loader.load_mcp_tools({"srv": {}}, user_id="alice")
    finally:
        loader.configure_tool_interceptor_factory(None)  # type: ignore[arg-type]

    assert isinstance(captured["interceptors"][0], PolicyToolInterceptor)
    assert captured["interceptors"][0]._user_id == "alice"


async def test_tool_authz_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_load(name: str, conn: dict, timeout: float, interceptors: Any) -> list:
        captured["interceptors"] = interceptors
        return _fake_tools(name)

    monkeypatch.setattr(loader, "_load_server_tools", fake_load)
    await loader.load_mcp_tools({"srv": {}})
    assert captured["interceptors"] is None


# --- shared connection validation (all tiers) ---------------------------------


def test_validate_accepts_well_formed_stdio_and_http() -> None:
    assert loader.validate_connection_dict("acme-kb", {"transport": "stdio", "command": "python", "args": []}) == []
    assert loader.validate_connection_dict("kb", {"transport": "streamable_http", "url": "https://x.com/mcp"}) == []


def test_validate_rejects_unknown_transport() -> None:
    problems = loader.validate_connection_dict("kb", {"transport": "stdioo", "command": "x"})
    assert any("unknown transport" in p for p in problems)


def test_validate_rejects_missing_required_keys() -> None:
    assert any(
        "missing required key 'command'" in p for p in loader.validate_connection_dict("kb", {"transport": "stdio"})
    )
    assert any(
        "missing required key 'url'" in p
        for p in loader.validate_connection_dict("kb", {"transport": "streamable_http"})
    )


def test_validate_rejects_underscore_names() -> None:
    """Underscores collide in the MCP_SERVER__* env mapping — names must be
    lowercase letters, digits, hyphens (same charset as the hub enforces)."""
    problems = loader.validate_connection_dict("acme_kb", {"transport": "stdio", "command": "x"})
    assert any("no underscores" in p for p in problems)


def test_validate_rejects_non_absolute_urls() -> None:
    problems = loader.validate_connection_dict("kb", {"transport": "sse", "url": "not-a-url"})
    assert any("absolute" in p for p in problems)


def test_env_override_with_invalid_shape_falls_back_to_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit but broken env override must not silently mask a valid
    registry entry — it warns loudly, then falls back."""
    monkeypatch.setenv("MCP_SERVER__ACME_KB", '{"transport": "stdioo"}')
    resolved = loader.resolve_mcp_connections(["acme-kb"])
    # registry entry won (its "python" command normalized to the interpreter)
    import sys

    assert resolved["acme-kb"]["command"] == sys.executable


def test_invalid_registry_entry_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "load_mcp_servers_config", lambda: {"bad-kb": {"transport": "stdioo", "command": "x"}})
    assert loader.resolve_mcp_connections(["bad-kb"]) == {}


async def test_probe_surfaces_config_problems(monkeypatch: pytest.MonkeyPatch) -> None:
    """The debug probe must show the config error, not a doomed handshake."""
    from agent_server.controller.http.routers import mcp as mcp_router_mod

    monkeypatch.setattr(
        mcp_router_mod, "load_mcp_servers_config", lambda: {"bad-kb": {"transport": "stdioo", "command": "x"}}
    )
    monkeypatch.setattr(mcp_router_mod, "env_override_names", lambda: [])
    results = await mcp_router_mod.list_mcp_servers()
    assert results[0].status == "error"
    assert "unknown transport" in results[0].error
