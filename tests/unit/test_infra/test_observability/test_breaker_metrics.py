"""Unit tests for the circuit-breaker and MCP-tool-load metric series."""

from typing import Any

import pytest

from agent_server.infra.circuit_breaker import CircuitBreaker, MemoryBreakerStorage
from agent_server.infra.observability.metrics import (
    CIRCUIT_BREAKER_OPEN,
    CIRCUIT_BREAKER_TRIPS,
    MCP_TOOL_LOADS,
    MCP_TOOLS_EXPOSED,
)
from agent_server.repo.graphs import mcp_loader

_LABEL = "metrics-test-svc"


def _breaker(*, fail_max: int = 2, cooldown: float = 60.0, label: str = _LABEL) -> CircuitBreaker:
    return CircuitBreaker(
        "mcp:internal-key", fail_max=fail_max, cooldown_secs=cooldown, storage=MemoryBreakerStorage(), label=label
    )


def _gauge(label: str = _LABEL) -> float:
    return CIRCUIT_BREAKER_OPEN.labels(name=label)._value.get()  # noqa: SLF001 — the value is the assertion


def _trips(label: str = _LABEL) -> float:
    return CIRCUIT_BREAKER_TRIPS.labels(name=label)._value.get()  # noqa: SLF001


async def _ok() -> str:
    return "fine"


async def _boom() -> str:
    raise ConnectionError("down")


async def test_a_new_breaker_renders_as_closed() -> None:
    """An absent series and a closed circuit must not look the same on a dashboard."""
    _breaker()

    assert _gauge() == 0


async def test_opening_and_recovering_move_the_gauge() -> None:
    # Negative cooldown: the open window is already past, so the next call is
    # the half-open probe — which is how a breaker ever closes again.
    breaker = _breaker(fail_max=2, cooldown=-1.0)

    for _ in range(2):
        with pytest.raises(ConnectionError):
            await breaker.call(_boom)

    assert _gauge() == 1
    assert _trips() == 1

    assert await breaker.call(_ok) == "fine"

    assert _gauge() == 0
    assert _trips() == 1  # a recovery is not a trip


async def test_the_label_is_what_dashboards_see_not_the_registry_key() -> None:
    """The registry key carries an endpoint fingerprint; the label must not."""
    breaker = _breaker(fail_max=1, cooldown=-1.0, label="acme-kb")

    with pytest.raises(ConnectionError):
        await breaker.call(_boom)

    assert CIRCUIT_BREAKER_OPEN.labels(name="acme-kb")._value.get() == 1  # noqa: SLF001


def _counter(counter: Any, **labels: str) -> float:
    return counter.labels(**labels)._value.get()  # noqa: SLF001


async def test_tool_load_outcomes_are_counted_by_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dashboard must tell 'auth expired' from 'server down'."""

    async def fake_load(name: str, conn: dict, timeout: float, interceptors: Any) -> list:
        if name == "bad":
            raise ConnectionError("refused")
        return []

    monkeypatch.setattr(mcp_loader, "_load_server_tools", fake_load)
    spec = mcp_loader.McpConnectionSpec.of({})
    before_ok = _counter(MCP_TOOL_LOADS, server="good", outcome="ok")
    before_err = _counter(MCP_TOOL_LOADS, server="bad", outcome="unreachable")

    await mcp_loader.load_mcp_tools({"good": spec, "bad": spec})

    assert _counter(MCP_TOOL_LOADS, server="good", outcome="ok") == before_ok + 1
    assert _counter(MCP_TOOL_LOADS, server="bad", outcome="unreachable") == before_err + 1


async def test_exposed_tool_count_reflects_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_load(name: str, conn: dict, timeout: float, interceptors: Any) -> list:
        return [*_tools("keep"), *_tools("drop")]

    monkeypatch.setattr(mcp_loader, "_load_server_tools", fake_load)
    mcp_loader.MCP_TOOLS_EXPOSED.labels(server="narrowed").set(99)

    await mcp_loader.load_mcp_tools({"narrowed": mcp_loader.McpConnectionSpec.of({}, ["keep"])})

    assert MCP_TOOLS_EXPOSED.labels(server="narrowed")._value.get() == 1  # noqa: SLF001


def _tools(name: str) -> list:
    from langchain_core.tools import BaseTool

    class _T(BaseTool):
        name: str
        description: str

        def _run(self, **kwargs: Any) -> Any:
            raise NotImplementedError

        async def _arun(self, **kwargs: Any) -> Any:
            return kwargs

    return [_T(name=name, description=f"{name} tool")]
