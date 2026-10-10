"""The server-middleware seam's two halves: graphs consume it, the guard keeps them.

``agent_server/repo/graphs/agent_middleware.py`` is worthless if no graph uses
it, and a convention that only the current graph follows decays with the next
graph. So there are two tests here: one proves the shipped graph picks up what
the application registers, the other fails CI when a composed agent is built
without the seam.
"""

import ast
import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent_server.repo.graphs.agent_middleware import (
    clear_agent_middleware,
    register_agent_middleware,
    server_middleware,
)
from shared.middleware.audit_log import AuditLogMiddleware

_GRAPHS_DIR = Path(__file__).resolve().parents[2] / "src" / "graphs"
_COMPOSED_AGENT_CALLS = {"create_deep_agent", "create_agent"}


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    clear_agent_middleware()
    yield
    clear_agent_middleware()


def test_a_registered_server_middleware_reaches_the_built_agent() -> None:
    """Registering once at the application layer is enough — the graph picks it up."""
    created: list[str] = []

    def _factory() -> AuditLogMiddleware:
        created.append("audit")
        return AuditLogMiddleware()

    register_agent_middleware(_factory)

    from research_agent.agent import build_research_agent

    assert build_research_agent() is not None
    assert created == ["audit"]


def _composed_agent_calls() -> list[ast.Call]:
    calls: list[ast.Call] = []
    for path in sorted(_GRAPHS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] in _COMPOSED_AGENT_CALLS:
                calls.append(node)
    return calls


def test_every_composed_agent_composes_server_middleware() -> None:
    """A graph that builds an agent without the seam runs unpoliced and silently."""
    calls = _composed_agent_calls()
    assert calls, "scan found no composed-agent call — the guard is looking in the wrong place"

    for call in calls:
        middleware = next((kw.value for kw in call.keywords if kw.arg == "middleware"), None)
        assert middleware is not None, f"line {call.lineno}: composed agent without middleware="
        assert "compose_middleware" in ast.unparse(middleware), (
            f"line {call.lineno}: build the middleware list with compose_middleware(...) "
            "so server policy applies to this agent"
        )


def test_reloading_the_app_does_not_stack_policy() -> None:
    """Re-registering the same policy must not duplicate it.

    Reloading an app module produces new function objects for the same policy;
    the agent builder rejects a duplicated middleware list outright, so the
    registry keys on module + qualified name instead of object identity.
    """
    import http_app

    importlib.reload(http_app)
    importlib.reload(http_app)

    names = [type(instance).__name__ for instance in server_middleware()]

    assert len(names) == len(set(names)), names


def test_the_shipped_app_registers_its_policy() -> None:
    """The composition root is where deployments add their own policy.

    Reloaded rather than imported: another test may already have imported the
    module, and this test just cleared the registry — what matters is that
    executing the module wires the policy.
    """
    import http_app

    importlib.reload(http_app)

    instances: list[Any] = server_middleware()
    assert instances, "the composed app must register server middleware"
    assert any(isinstance(instance, AuditLogMiddleware) for instance in instances)
