"""Graph factory classification, runtime construction, and dispatch helpers.

Supports four factory signatures:
- 0 params: ``def make_graph() -> Graph``
- 1 param (config): ``def make_graph(config: RunnableConfig) -> Graph``
- 1 param (runtime): ``def make_graph(runtime: ServerRuntime) -> Graph``
- 2 params (either order): ``def make_graph(config, runtime: ServerRuntime)``

Factory detection happens at graph load time via ``classify_factory()``.
Per-request invocation is handled by ``invoke_factory()`` with the appropriate
``ServerRuntime`` variant constructed by ``build_server_runtime()``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import typing
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args, get_origin

import structlog
from langgraph.graph import StateGraph
from langgraph.pregel import Pregel
from langgraph.store.base import BaseStore
from langgraph_sdk.auth.types import BaseUser
from langgraph_sdk.runtime import (
    ServerRuntime,
    _ExecutionRuntime,
    _ReadRuntime,
)
from pydantic import BaseModel

from agent_server.auth.ctx import get_auth_ctx
from agent_server.domain.user import User

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

AccessContext = Literal[
    "threads.create_run",
    "threads.update",
    "threads.read",
    "assistants.read",
]
"""Why the graph factory is being called.

- ``threads.create_run``: Full graph execution (nodes + edges).
- ``threads.update``: ``aupdate_state`` — applies writes to state channels.
- ``threads.read``: ``aget_state`` / ``aget_state_history`` — formats state snapshots.
- ``assistants.read``: Schema extraction, graph visualization.
"""

_HookType = Callable[["_RunnableConfig", "ServerRuntime"], dict[str, Any]]

# Maps graph_id → a callable that produces kwargs for the factory function.
# Populated by ``classify_factory()`` at graph load time.
_FACTORY_KWARGS: dict[str, _HookType] = {}


# Maps graph_id → the ``T`` from ``ServerRuntime[T]`` (or ``None`` if
# the factory uses plain ``ServerRuntime`` without parameterization).
# Populated by ``classify_factory()`` alongside ``_FACTORY_KWARGS``.
_FACTORY_CONTEXT_TYPES: dict[str, type | None] = {}

# Concrete runtime classes used for ``issubclass`` checks during classification.
_RUNTIME_CLASSES: tuple[type, ...] = (_ExecutionRuntime, _ReadRuntime)

# Type alias for RunnableConfig — LangGraph uses ``dict[str, Any]``.
_RunnableConfig = dict[str, Any]

# Namespace for resolving string annotations in factory functions.
# Lets users import ``ServerRuntime`` inside ``TYPE_CHECKING`` blocks
# and still have the factory classifier resolve it correctly.
_RUNTIME_LOCALNS: dict[str, Any] = {
    "ServerRuntime": ServerRuntime,
    "RunnableConfig": _RunnableConfig,
    "Config": _RunnableConfig,
}


# ---------------------------------------------------------------------------
# Factory classification
# ---------------------------------------------------------------------------


def classify_factory(fn: Callable, graph_id: str) -> None:
    """Inspect *fn*'s signature and register a dispatch hook if it accepts arguments.

    Idempotent — calling twice with the same *graph_id* is a no-op.

    Also extracts the ``T`` from ``ServerRuntime[T]`` annotations and stores
    it in ``_FACTORY_CONTEXT_TYPES`` so that ``coerce_context()`` can coerce
    the raw request context dict to ``T`` at invocation time.

    Args:
        fn: The callable graph export (factory function).
        graph_id: The graph identifier from the configuration file.
    """
    if graph_id in _FACTORY_KWARGS:
        return
    hook, context_type = _classify_factory(fn)
    if hook is not None:
        _FACTORY_KWARGS[graph_id] = hook
        _FACTORY_CONTEXT_TYPES[graph_id] = context_type


def _is_runtime_annotation(annotation: Any) -> bool:
    """Return ``True`` if *annotation* refers to ``ServerRuntime`` or a concrete subclass.

    Handles:
    - The ``ServerRuntime`` TypeAliasType directly.
    - Parameterized forms like ``ServerRuntime[MyContext]``.
    - Concrete runtime classes (``_ExecutionRuntime``, ``_ReadRuntime``)
      and their subclasses.
    - ``Annotated[ServerRuntime, ...]`` wrappers.
    """
    if annotation is inspect.Parameter.empty:
        return False
    # Identity check against the ServerRuntime TypeAliasType
    if annotation is ServerRuntime:
        return True
    # issubclass check against concrete runtime classes
    if isinstance(annotation, type):
        try:
            return issubclass(annotation, _RUNTIME_CLASSES)
        except TypeError:
            return False
    # Handle parameterized types (ServerRuntime[MyContext], Annotated[...], Union)
    origin = get_origin(annotation)
    if origin is not None:
        if origin is ServerRuntime:
            return True
        # For Union (e.g. None | ServerRuntime) or Annotated, check all operands
        args = get_args(annotation)
        if args:
            return any(_is_runtime_annotation(a) for a in args)
    return False


def _extract_context_type(annotation: Any) -> type | None:
    """Extract ``T`` from a ``ServerRuntime[T]`` annotation.

    Returns:
        The context type ``T`` if the annotation is ``ServerRuntime[T]``
        (including ``None | ServerRuntime[T]``), or ``None`` if the
        annotation is plain ``ServerRuntime`` or not a runtime annotation.
    """
    if annotation is inspect.Parameter.empty or annotation is ServerRuntime:
        return None

    origin = get_origin(annotation)
    if origin is ServerRuntime:
        args = get_args(annotation)
        if args:
            return args[0]
        return None

    # Handle Union types (e.g. None | ServerRuntime[T])
    if origin is not None:
        args = get_args(annotation)
        if args:
            for arg in args:
                result = _extract_context_type(arg)
                if result is not None:
                    return result

    return None


def _resolve_hints(fn: Callable) -> dict[str, Any]:
    """Resolve string annotations using the function's module globals + runtime types."""
    try:
        return typing.get_type_hints(fn, localns=_RUNTIME_LOCALNS, include_extras=True)
    except (NameError, AttributeError) as exc:
        logger.debug("graph_factory_hint_resolution_failed", fn=fn, exc=str(exc))
        return {}


def _classify_factory(fn: Callable) -> tuple[_HookType | None, type | None]:
    """Classify a graph factory by its parameter signature.

    Per-parameter classification (the FastAPI ``analyze_param`` shape): each
    parameter gets a role — ``config`` or ``runtime`` (annotated
    ``ServerRuntime``) — then one uniform assembler builds the invocation kwargs.

    Returns a tuple of:
    - A callable that, given ``(config, server_runtime)``, produces the
      ``**kwargs`` dict to pass to the factory. ``None`` for 0-param factories.
    - The context type ``T`` from ``ServerRuntime[T]`` (or ``None``).

    Raises:
        ValueError: On duplicate config/runtime params.
    """
    hints = _resolve_hints(fn)
    specs = [_classify_param(p, hints) for p in inspect.signature(fn).parameters.values()]
    if not specs:
        return None, None

    _validate_specs(fn, specs)

    def hook(config: _RunnableConfig, runtime: ServerRuntime) -> dict[str, Any]:
        return {spec.name: _param_value(spec, config, runtime) for spec in specs}

    ctx_type = next(
        (_extract_context_type(spec.annotation) for spec in specs if spec.kind == "runtime"),
        None,
    )
    return hook, ctx_type


@dataclass(frozen=True)
class _ParamSpec:
    """One factory parameter and its role."""

    name: str
    kind: str  # "config" | "runtime"
    annotation: Any


def _classify_param(p: inspect.Parameter, hints: dict[str, Any]) -> _ParamSpec:
    """One parameter in, its role out (FastAPI's analyze_param shape)."""
    annotation = hints.get(p.name, p.annotation)
    if _is_runtime_annotation(annotation):
        return _ParamSpec(p.name, "runtime", annotation)
    return _ParamSpec(p.name, "config", annotation)


def _validate_specs(fn: Callable, specs: list[_ParamSpec]) -> None:
    """At most one config and one runtime parameter; injectables are free."""
    configs = [s.name for s in specs if s.kind == "config"]
    runtimes = [s.name for s in specs if s.kind == "runtime"]
    if len(configs) > 1:
        raise ValueError(
            f"Graph factory {fn} has {len(configs)} config parameters {configs}; "
            f"at most one is allowed. For a 2-param factory, one parameter must "
            f"be typed as ServerRuntime."
        )
    if len(runtimes) > 1:
        raise ValueError(
            f"Graph factory {fn} has {len(runtimes)} parameters both annotated as "
            f"ServerRuntime {runtimes}. Expected one ServerRuntime and one "
            f"RunnableConfig."
        )


def _param_value(spec: _ParamSpec, config: _RunnableConfig, runtime: ServerRuntime) -> Any:
    """Resolve one parameter's invocation value."""
    return config if spec.kind == "config" else runtime


# ---------------------------------------------------------------------------
# Factory state queries
# ---------------------------------------------------------------------------


def is_factory(graph_id: str) -> bool:
    """Return ``True`` if *graph_id* was classified as a factory that accepts arguments."""
    return graph_id in _FACTORY_KWARGS


def get_factory_hook(graph_id: str) -> _HookType | None:
    """The dispatch hook for a graph, including load-time-only (mcp_tools) hooks."""
    return _FACTORY_KWARGS.get(graph_id)


def is_for_execution(access_context: AccessContext) -> bool:
    """Return ``True`` if the access context represents full graph execution."""
    return access_context == "threads.create_run"


# ---------------------------------------------------------------------------
# Context coercion
# ---------------------------------------------------------------------------


def coerce_context(context: dict[str, Any] | None, graph_id: str) -> Any:
    """Coerce a raw context dict to the factory's declared context type ``T``.

    If the factory declared ``ServerRuntime[T]``, the raw dict is converted
    to an instance of ``T``:
    - Pydantic ``BaseModel`` → ``T.model_validate(context)``
    - ``dataclass`` → ``T(**context)``

    On failure (e.g., validation error or missing fields), logs a warning
    and returns the raw dict for graceful degradation.

    Args:
        context: The raw context dict from the request, or ``None``.
        graph_id: The graph identifier (used to look up the context type).

    Returns:
        A coerced ``T`` instance, the raw dict, or ``None``.
    """
    if context is None:
        return None

    ctx_type = _FACTORY_CONTEXT_TYPES.get(graph_id)
    if ctx_type is None:
        return context

    try:
        if _is_pydantic_model(ctx_type):
            return cast("type[BaseModel]", ctx_type).model_validate(context)
        if dataclasses.is_dataclass(ctx_type):
            return ctx_type(**context)
    except Exception as exc:
        logger.warning(
            "context_coercion_failed",
            graph_id=graph_id,
            context_type=ctx_type.__name__,
            exc=str(exc),
            msg="Falling back to raw dict",
        )
    return context


def _is_pydantic_model(cls: type) -> bool:
    """Return ``True`` if *cls* is a Pydantic ``BaseModel`` subclass.

    Uses duck-typing (``model_validate``) to avoid importing pydantic
    directly, which is an optional dependency at the service layer.
    """
    return hasattr(cls, "model_validate") and callable(getattr(cls, "model_validate", None))


# ---------------------------------------------------------------------------
# Runtime construction
# ---------------------------------------------------------------------------


def build_server_runtime(
    *,
    access_context: AccessContext,
    store: BaseStore | None,
    user: User | BaseUser | None = None,
    context: Any = None,
) -> ServerRuntime:
    """Construct the appropriate ``ServerRuntime`` variant for the access context.

    For ``threads.create_run``, returns an ``_ExecutionRuntime`` (which has
    a ``context`` field populated with the coerced request context).
    For all other contexts, returns a ``_ReadRuntime`` (no ``context`` field).

    If *user* is ``None``, falls back to the current request's auth context.

    Args:
        access_context: Why the graph factory is being called.
        store: The persistence store for the graph run.
        user: The authenticated user, or ``None`` to auto-detect from auth context.
        context: The (optionally coerced) request context for the factory.
            Only used for ``_ExecutionRuntime``.

    Returns:
        A ``ServerRuntime`` instance (either ``_ExecutionRuntime`` or ``_ReadRuntime``).
    """
    if user is None:
        auth_ctx = get_auth_ctx()
        user = auth_ctx.user if auth_ctx else None

    if is_for_execution(access_context):
        return _ExecutionRuntime(
            access_context=access_context,
            user=user,
            store=cast("BaseStore", store),  # initialized before execution-time runtimes are built
            context=context,
        )
    return _ReadRuntime(
        access_context=access_context,
        user=user,
        store=cast("BaseStore", store),
    )


# ---------------------------------------------------------------------------
# Factory invocation
# ---------------------------------------------------------------------------


def invoke_factory(
    fn: Callable,
    graph_id: str,
    config: _RunnableConfig,
    server_runtime: ServerRuntime,
) -> Any:
    """Call a graph factory with the correct arguments based on its classification.

    Args:
        fn: The graph factory callable.
        graph_id: The graph identifier (used to look up the dispatch hook).
        config: The ``RunnableConfig`` dict for this request.
        server_runtime: The ``ServerRuntime`` for this request.

    Returns:
        Whatever the factory returns (``Pregel``, ``StateGraph``, coroutine,
        async context manager, etc.).
    """
    hook = _FACTORY_KWARGS.get(graph_id)
    if not hook:
        return fn()
    kwargs = hook(config, server_runtime)
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Graph result resolution
# ---------------------------------------------------------------------------


@asynccontextmanager
async def generate_graph(value: Any, graph_id: str) -> AsyncIterator[Pregel | StateGraph]:
    """Yield a graph object regardless of the factory's return type.

    Handles:
    - ``Pregel`` / ``StateGraph`` — yield directly.
    - Async context manager — ``async with value as graph: yield graph``.
    - Sync context manager — ``with value as graph: yield graph``.
    - Coroutine — ``yield await value``.
    - Other — yield as-is (likely a ``StateGraph`` or ``Pregel``).

    Args:
        value: The raw return value from a graph factory or a static graph.
        graph_id: The graph identifier (used for logging).

    Yields:
        A graph object (``Pregel`` or ``StateGraph``).
    """
    if isinstance(value, Pregel | StateGraph):
        yield value
    elif hasattr(value, "__aenter__") and hasattr(value, "__aexit__"):
        async with value as ctx_value:
            logger.debug("graph_factory_async_ctx_resolved", graph_id=graph_id)
            yield ctx_value
    elif hasattr(value, "__enter__") and hasattr(value, "__exit__"):
        with value as ctx_value:
            logger.debug("graph_factory_sync_ctx_resolved", graph_id=graph_id)
            yield ctx_value
    elif asyncio.iscoroutine(value):
        result = await value
        logger.debug("graph_factory_coroutine_resolved", graph_id=graph_id)
        yield result
    else:
        yield value


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def clear_factory_registry(graph_id: str | None = None) -> None:
    """Remove factory dispatch hooks and context type registrations.

    Args:
        graph_id: Specific graph to clear, or ``None`` to clear all.
    """
    if graph_id is not None:
        _FACTORY_KWARGS.pop(graph_id, None)
        _FACTORY_CONTEXT_TYPES.pop(graph_id, None)
    else:
        _FACTORY_KWARGS.clear()
        _FACTORY_CONTEXT_TYPES.clear()
