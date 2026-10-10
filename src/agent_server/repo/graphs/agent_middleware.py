"""Server-side agent middleware seam.

A composed agent (deepagents ``create_deep_agent`` / langchain ``create_agent``)
takes its middleware list from the graph author. That makes *server* policy —
audit trails, quotas, per-user hygiene — opt-in: a graph that forgets to add it
runs unpoliced, and nothing notices. This registry inverts the dependency: the
application layer registers middleware factories once, and graph builders
compose them through ``compose_middleware()``, so every agent carries server
policy regardless of who wrote the graph.

Deliberately a registry of **factories**, not instances: middleware can hold
per-run state, and a factory graph is rebuilt per run. Registration is explicit
wiring in the application layer (``src/http_app.py``) — this server has no
entry-point plugins, and a policy seam that activates itself would be worse than
one that is wired in the open.

Graphs reach this through ``agent_server.contracts`` (the only framework module
they may import); ``tests/unit/test_repo/test_agent_middleware.py`` and the
guard in ``tests/graphs/test_agent_middleware_seam.py`` pin both halves.
"""

from collections.abc import Callable
from typing import Any

import structlog

logger = structlog.getLogger(__name__)

# A factory returns a fresh AgentMiddleware instance for one graph build.
MiddlewareFactory = Callable[[], Any]

_registry: list[str] = []
_factories: dict[str, MiddlewareFactory] = {}


def _factory_key(factory: MiddlewareFactory) -> str:
    """A stable identity for a factory, surviving module reloads.

    Object identity is not enough: reloading the module that registers a
    middleware produces a *new* function object for the same policy, and the
    stack would grow a duplicate on every reload — which the agent builder
    rejects outright ("duplicate middleware instances"). Module plus qualified
    name is what stays constant across a reload; anonymous factories keep
    identity so distinct lambdas stay distinct.
    """
    module = getattr(factory, "__module__", "?")
    name = getattr(factory, "__qualname__", "")
    if not name or name.endswith("<lambda>"):
        # Anonymous: identity is all we have. Collapsing distinct lambdas into
        # one entry would silently drop policy, so they never dedupe.
        return f"{module}.<anonymous>@{id(factory)}"
    return f"{module}.{name}"


def register_agent_middleware(factory: MiddlewareFactory) -> None:
    """Add a middleware factory to the server policy stack (idempotent).

    Idempotent by :func:`_factory_key`, so a module reload or a second
    ``create_app()`` in one process cannot stack the same policy twice.
    """
    key = _factory_key(factory)
    if any(existing == key for existing in _registry):
        return
    _registry.append(key)
    _factories[key] = factory
    logger.info("agent_middleware_registered", factory=key)


def clear_agent_middleware() -> None:
    """Drop every registered factory (tests; not needed at runtime)."""
    _registry.clear()
    _factories.clear()


def server_middleware() -> list[Any]:
    """Fresh instances from every registered factory, in registration order."""
    return [_factories[key]() for key in _registry]


def compose_middleware(*graph_middleware: Any) -> list[Any]:
    """The list a composed agent passes to ``middleware=``.

    Server policy first: middleware wraps in order, so the outermost entries
    see every call the inner ones make — the server's view stays complete even
    when a graph adds its own.
    """
    return [*server_middleware(), *graph_middleware]
