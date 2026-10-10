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
    monkeypatch.setenv("SANDBOX_PROVIDER", "monty")

    from research_agent.agent import build_backend

    first = build_backend()
    second = build_backend()

    assert first.default is not second.default
