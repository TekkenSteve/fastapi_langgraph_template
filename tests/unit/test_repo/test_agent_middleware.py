"""Unit tests for the server-side agent middleware seam."""

from typing import Any

import pytest

from agent_server.repo.graphs.agent_middleware import (
    clear_agent_middleware,
    compose_middleware,
    register_agent_middleware,
    server_middleware,
)


@pytest.fixture(autouse=True)
def _clean() -> Any:
    clear_agent_middleware()
    yield
    clear_agent_middleware()


def test_nothing_is_registered_by_default() -> None:
    """The framework ships no policy of its own — the application wires it."""
    assert server_middleware() == []


def test_registration_order_is_preserved() -> None:
    register_agent_middleware(lambda: "first")
    register_agent_middleware(lambda: "second")

    assert server_middleware() == ["first", "second"]


def test_a_factory_yields_a_fresh_instance_per_graph_build() -> None:
    """Middleware can hold per-run state, and a factory graph is rebuilt per run."""

    class _Marker:
        pass

    register_agent_middleware(_Marker)

    first = server_middleware()[0]
    second = server_middleware()[0]

    assert isinstance(first, _Marker)
    assert first is not second


def test_registering_the_same_factory_twice_is_a_no_op() -> None:
    def _factory() -> str:
        return "x"

    register_agent_middleware(_factory)
    register_agent_middleware(_factory)

    assert server_middleware() == ["x"]


def test_compose_puts_server_policy_first() -> None:
    """Middleware wraps in order, so the server must be outermost."""
    register_agent_middleware(lambda: "server")

    assert compose_middleware("graph", "more") == ["server", "graph", "more"]


def test_clear_drops_every_factory() -> None:
    register_agent_middleware(lambda: "x")

    clear_agent_middleware()

    assert server_middleware() == []
