"""research_agent's MCP contract: one-line composition at the entry."""

from typing import Any

import pytest

from research_agent.graph import graph


def test_entry_is_a_per_run_factory() -> None:
    """A per-run factory is what keeps the agent's sandbox off other runs."""
    import inspect

    assert inspect.iscoroutinefunction(graph)
    assert list(inspect.signature(graph).parameters) == ["config"]


def test_factory_builds_with_injected_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import agent_server.repo.graphs.mcp_loader as loader

    async def _no_tools(connections: dict[str, Any], *, user_id: str | None = None, interceptors: Any = None) -> list:
        return []

    monkeypatch.setattr(loader, "load_mcp_tools", _no_tools)
    built = asyncio.run(graph({"configurable": {"user_id": "u1"}}))
    assert built is not None


def test_each_build_gets_a_fresh_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent owns its sandbox backend; a shared one leaks files across runs.

    With the entry a load-time-compiled graph the backend (and, under
    SANDBOX_PROVIDER=monty, its file table) was reused by every run and user.
    """
    import agent_server.contracts as contracts

    monkeypatch.setattr(contracts.settings.sandbox, "SANDBOX_PROVIDER", "monty")

    from research_agent.agent import build_backend

    first = build_backend()
    second = build_backend()

    assert first.default is not second.default


def _user_connections(monkeypatch: pytest.MonkeyPatch, rows: list[Any]) -> list[dict[str, Any]]:
    """Point the hub's graph-side loader at *rows* and capture what the loader is asked to load."""
    import agent_server.repo.graphs.mcp_loader as loader
    from hub import queries

    class _Result:
        def scalars(self) -> "_Result":
            return self

        def all(self) -> list[Any]:
            return rows

    class _Session:
        async def execute(self, _stmt: Any) -> _Result:
            return _Result()

    class _SessionMaker:
        async def __aenter__(self) -> _Session:
            return _Session()

        async def __aexit__(self, *exc: Any) -> bool:
            return False

    def _maker() -> _SessionMaker:
        return _SessionMaker()

    monkeypatch.setattr(queries, "get_session_maker", lambda: _maker)
    loader.clear_mcp_tools_cache()
    captured: list[dict[str, Any]] = []

    async def _capture(connections: dict[str, Any], *, user_id: str | None = None, interceptors: Any = None) -> list:
        captured.append(connections)
        return []

    monkeypatch.setattr(loader, "load_mcp_tools", _capture)
    return captured


def test_a_users_own_connection_wins_over_the_deployment_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry is wired to the hub user tier — a name the user connected
    resolves to their endpoint, not the shared registry one."""
    import asyncio
    from types import SimpleNamespace

    row = SimpleNamespace(
        user_id="u1",
        name="acme-kb",
        transport="streamable_http",
        url="https://user.example.com/kb",
        auth_type="none",
        headers={},
        allowed_tools=None,
        enabled=True,
    )
    captured = _user_connections(monkeypatch, [row])

    built = asyncio.run(graph({"configurable": {"user_id": "u1"}}))

    assert built is not None
    assert [spec.connection for spec in captured[0].values()] == [
        {"transport": "streamable_http", "url": "https://user.example.com/kb"}
    ]


def test_a_user_without_connections_falls_back_to_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    captured = _user_connections(monkeypatch, [])

    asyncio.run(graph({"configurable": {"user_id": "u1"}}))

    assert captured[0]["acme-kb"].connection["transport"] == "stdio"


def test_a_disabled_user_connection_blocks_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turning a connection off must not silently mean "use the shared one"."""
    import asyncio
    from types import SimpleNamespace

    row = SimpleNamespace(
        user_id="u1",
        name="acme-kb",
        transport="streamable_http",
        url="https://user.example.com/kb",
        auth_type="none",
        headers={},
        allowed_tools=None,
        enabled=False,
    )
    captured = _user_connections(monkeypatch, [row])

    asyncio.run(graph({"configurable": {"user_id": "u1"}}))

    assert captured == []
