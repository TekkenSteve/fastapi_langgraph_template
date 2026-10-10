"""FastAPI application assembly (composition root)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.requests import HTTPConnection

from agent_server import __version__
from agent_server.app.app_loader import load_custom_app
from agent_server.app.route_merger import (
    merge_exception_handlers,
    merge_lifespans,
)
from agent_server.auth.deps import require_auth
from agent_server.auth.enforcement import apply_auth_enforcement
from agent_server.config.graph_config import (
    CorsConfig,
    HttpConfig,
    add_dependency_paths,
    get_config_dir,
    load_config,
    load_http_config,
)
from agent_server.config.settings import settings
from agent_server.controller.http.middleware import (
    ContentTypeFixMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    StructLogMiddleware,
)
from agent_server.controller.http.routers.assistants import router as assistants_router
from agent_server.controller.http.routers.audit import router as audit_router
from agent_server.controller.http.routers.crons import router as crons_router
from agent_server.controller.http.routers.event_streaming import router as event_streaming_router
from agent_server.controller.http.routers.health import router as health_router
from agent_server.controller.http.routers.mcp import router as mcp_router
from agent_server.controller.http.routers.runs import router as runs_router
from agent_server.controller.http.routers.stateless_runs import router as stateless_runs_router
from agent_server.controller.http.routers.store import router as store_router
from agent_server.controller.http.routers.threads import router as threads_router
from agent_server.domain.errors import AgentProtocolError, get_error_type
from agent_server.infra.logging import setup_logging
from agent_server.infra.observability.metrics import setup_prometheus_metrics
from agent_server.infra.observability.setup import setup_observability
from agent_server.infra.redis import redis_manager
from agent_server.repo.database import db_manager
from agent_server.repo.graphs.langgraph_service import get_langgraph_service
from agent_server.repo.migrations import run_migrations_async
from agent_server.usecase.cron.scheduler import cron_scheduler
from agent_server.usecase.execution.executor import executor
from agent_server.usecase.execution.lease_reaper import lease_reaper
from agent_server.usecase.execution.run_preparation import get_default_durability
from agent_server.usecase.streaming.broker import broker_manager
from agent_server.usecase.thread_ttl import get_thread_ttl_config, thread_ttl_sweeper

OPENAPI_TAGS: list[dict[str, Any]] = [
    {"name": "Assistants", "description": "A configured instance of a graph."},
    {"name": "Threads", "description": "Accumulated state and outputs from a group of runs."},
    {"name": "Thread Runs", "description": "Invoke a graph on a thread, updating its persistent state."},
    {"name": "Stateless Runs", "description": "Invoke a graph without state or memory persistence."},
    {"name": "Crons", "description": "Scheduled recurring runs on a cron schedule."},
    {"name": "Store", "description": "Persistent key-value and semantic storage available from any thread."},
    {"name": "Event Streaming", "description": "Agent Protocol v2 thread event streaming and commands."},
    {"name": "Health", "description": "Server health checks and service information."},
    {"name": "Audit", "description": "Query the audit ledger of agent actions (admin only)."},
]

setup_logging()
logger = structlog.getLogger(__name__)

# Default CORS headers required for LangGraph SDK stream reconnection
DEFAULT_EXPOSE_HEADERS = ["Content-Location", "Location"]


def _log_connection_help(error: Exception) -> None:
    """Log a helpful error message when database connection fails."""
    logger.error(
        "Could not connect to PostgreSQL",
        error=str(error),
        hint="Check your database configuration and ensure PostgreSQL is running.",
    )
    logger.error(
        "Troubleshooting tips:\n"
        "  - Local development?  Run 'make dev' (starts PostgreSQL + hot-reload server)\n"
        "  - Docker deployment?  Run 'docker compose up -d' (starts PostgreSQL + app)\n"
        "  - External database?  Check DATABASE_URL or POSTGRES_* vars in your .env\n"
        "  - Missing .env file?  Copy .env.example to .env and configure it"
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan context manager for startup/shutdown"""
    # Resolve the durability default up front: an invalid value fails the boot, not every run.
    get_default_durability()

    # Multi-pod K8s: set RUN_MIGRATIONS_ON_STARTUP=false + run `make migrate-up`
    # out-of-band.
    # Per-invocation MCP tool authorization: app layer injects the interceptor
    # factory (repo/graphs must not import auth).
    from agent_server.auth.policy import get_policy_engine
    from agent_server.auth.tool_authz import PolicyToolInterceptor
    from agent_server.repo.graphs.mcp_loader import configure_tool_interceptor_factory

    configure_tool_interceptor_factory(lambda user_id: PolicyToolInterceptor(get_policy_engine(), user_id))

    # Policy engine: OPA sidecar replaces the local engine when configured.
    if settings.policy.OPA_URL:
        from agent_server.auth.opa_policy import OpaPolicyEngine
        from agent_server.auth.policy import configure_policy_engine

        configure_policy_engine(OpaPolicyEngine(settings.policy.OPA_URL, package=settings.policy.OPA_POLICY_PACKAGE))
        logger.info("policy_engine_configured", backend="opa", url=settings.policy.OPA_URL)

    if settings.app.RUN_MIGRATIONS_ON_STARTUP:
        try:
            await run_migrations_async()
        except (ConnectionRefusedError, OSError) as e:
            _log_connection_help(e)
            raise
    else:
        logger.info("skipping startup migrations (RUN_MIGRATIONS_ON_STARTUP=false)")

    # Startup: Initialize database and LangGraph components
    try:
        await db_manager.initialize()
    except (ConnectionRefusedError, OSError) as e:
        _log_connection_help(e)
        raise

    # Observability
    setup_observability()

    # Initialize LangGraph service
    langgraph_service = get_langgraph_service()
    await langgraph_service.initialize()

    # Initialize Redis broker (if enabled)
    if settings.redis.REDIS_BROKER_ENABLED:
        try:
            await redis_manager.initialize()
        except (ConnectionError, OSError) as e:
            logger.error(
                "Cannot connect to Redis. "
                "Set REDIS_BROKER_ENABLED=false for single-instance mode without Redis, "
                "or ensure Redis is running at REDIS_URL.",
                redis_url=settings.redis.REDIS_URL,
                error=str(e),
            )
            raise
    else:
        logger.warning(
            "Running without Redis broker. Background runs have no crash recovery "
            "or horizontal scaling. Set REDIS_BROKER_ENABLED=true and configure "
            "REDIS_URL for production use.",
        )

    # Start broker manager (cleanup task for in-memory, cancel listener for Redis)
    await broker_manager.start()

    # Start executor (spawns worker coroutines when Redis is enabled)
    await executor.start()

    # Start lease reaper (recovers crashed worker runs, Redis mode only)
    if settings.redis.REDIS_BROKER_ENABLED:
        await lease_reaper.start()

    # Start cron scheduler (fires due cron jobs)
    if settings.cron.CRON_ENABLED:
        await cron_scheduler.start()

    # Start thread TTL sweeper (deletes/compacts expired threads); resolving
    # the config here also fails fast on an invalid retention policy.
    if get_thread_ttl_config() is not None:
        await thread_ttl_sweeper.start()

    yield

    # Shutdown order: ttl sweeper → cron → reaper → executor (drains jobs) → broker → Redis → DB
    if get_thread_ttl_config() is not None:
        await thread_ttl_sweeper.stop()
    if settings.cron.CRON_ENABLED:
        await cron_scheduler.stop()
    if settings.redis.REDIS_BROKER_ENABLED:
        await lease_reaper.stop()
    await executor.stop()
    await broker_manager.stop()

    # Close Redis broker (if enabled)
    if settings.redis.REDIS_BROKER_ENABLED:
        await redis_manager.close()

    await db_manager.close()


# Define core exception handlers
async def agent_protocol_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    """Convert HTTP exceptions to Agent Protocol error format"""
    return JSONResponse(
        status_code=exc.status_code,
        content=AgentProtocolError(
            error=get_error_type(exc.status_code),
            message=exc.detail,
            details=getattr(exc, "details", None),
        ).model_dump(),
    )


async def general_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions"""
    return JSONResponse(
        status_code=500,
        content=AgentProtocolError(
            error="internal_error",
            message="An unexpected error occurred",
            details=None,  # FIX: do not leak internal exception details
        ).model_dump(),
    )


exception_handlers: dict[type[Exception], Any] = {
    HTTPException: agent_protocol_exception_handler,
    Exception: general_exception_handler,
}


# Define root endpoint handler
async def root_handler() -> dict[str, str]:
    """Root endpoint"""
    return {
        "message": settings.app.PROJECT_NAME,
        "version": __version__,
        "status": "running",
    }


async def _require_auth_on_http(connection: HTTPConnection) -> None:
    """``require_auth`` for custom routes; websocket routes under an included router pass through."""
    # An included router's dependencies reach its websockets too, and a Request-typed
    # dependency there fails every connection with a TypeError.
    if isinstance(connection, Request):
        await require_auth(connection)


CUSTOM_ROUTE_AUTH = [Depends(_require_auth_on_http)]


def _apply_auth_to_custom_routes(app: FastAPI, auth_deps: list[Any]) -> int:
    """Prepend ``auth_deps`` to every FastAPI route the custom app declares.

    Must run before the core routers are included: health checks and the root
    stay public, and protocol routes carry their own auth. Returns the number
    of routes protected.
    """
    fastapi_pages = {app.openapi_url, app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url}
    protected = 0
    uncovered: list[str] = []

    def walk(routes: list[Any], context_changed: bool | None) -> None:
        """``context_changed`` is None outside an included router, else whether its context got ``auth_deps``."""
        nonlocal protected
        for route in routes:
            if isinstance(route, APIRoute):
                if context_changed is None:
                    protected += _prepend_dependencies(route, auth_deps)
                else:
                    protected += int(context_changed)
                continue
            # FastAPI wraps included routers; newer versions expose the wrapped
            # router as `original_router` rather than `routes`.
            nested = getattr(route, "original_router", None)
            if nested is not None:
                changed = _prepend_to_include_context(route, auth_deps) if context_changed is None else context_changed
                walk(list(nested.routes), changed)
                # Effective routes are cached until the wrapped router reports a change; a
                # schema built at import would otherwise keep serving them unauthenticated.
                getattr(nested, "_mark_routes_changed", lambda: None)()
            elif getattr(route, "routes", None):
                # A mounted app's routes never inherit an include context.
                walk(list(route.routes), None)
            elif getattr(route, "path", None) not in fastapi_pages:
                uncovered.append(getattr(route, "path", repr(route)))

    walk(list(app.routes), None)
    logger.info("Applied authentication dependency to custom routes", route_count=protected)
    if uncovered:
        # Plain Starlette routes and mounted non-FastAPI apps take no dependency, and
        # websockets are skipped by `_require_auth_on_http`; say so rather than imply cover.
        logger.warning("enable_custom_route_auth does not cover these custom routes", paths=uncovered)
    return protected


def _prepend_to_include_context(included: Any, deps: list[Any]) -> bool:
    """Put ``deps`` ahead of an included router's app- and include-level dependencies, once."""
    # On FastAPI >= 0.137 those run before every route's own list, so prepending
    # to the route alone would let them execute for unauthenticated requests.
    context = included.include_context
    if any(dep in context.dependencies for dep in deps):
        return False
    context.dependencies = [*deps, *context.dependencies]
    return True


def _prepend_dependencies(route: APIRoute, deps: list[Any]) -> int:
    """Prepend ``deps`` to one route, once. Returns 1 if the route changed."""
    if any(dep in route.dependencies for dep in deps):
        return 0
    route.dependencies = [*deps, *route.dependencies]
    # `dependencies` alone is only read at construction time; mirror what
    # APIRoute.__init__ does so the already-built dependant picks it up.
    for dep in reversed(deps):
        route.dependant.dependencies.insert(0, get_parameterless_sub_dependant(depends=dep, path=route.path_format))
    return 1


def _add_cors_middleware(app: FastAPI, cors_config: CorsConfig | None) -> None:
    """Add CORS middleware with config or defaults.

    When ``allow_origin_regex`` is configured, ``allow_origins`` defaults to an
    empty list so the regex is useful, and ``allow_credentials`` defaults to
    ``False``. Without a regex, existing origin and credential defaults apply.

    Args:
        app: FastAPI application instance
        cors_config: CORS configuration dict or None for defaults
    """
    if cors_config:
        allow_origin_regex = cors_config.get("allow_origin_regex")
        has_origin_regex = allow_origin_regex is not None
        origins = cors_config.get("allow_origins", [] if has_origin_regex else ["*"])
        credentials = cors_config.get(
            "allow_credentials",
            False if has_origin_regex else origins not in (["*"], "*"),
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_origin_regex=allow_origin_regex,
            allow_credentials=credentials,
            allow_methods=cors_config.get("allow_methods", ["*"]),
            allow_headers=cors_config.get("allow_headers", ["*"]),
            expose_headers=cors_config.get("expose_headers", DEFAULT_EXPOSE_HEADERS),
            max_age=cors_config.get("max_age", 600),
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=DEFAULT_EXPOSE_HEADERS,
        )


def _add_common_middleware(app: FastAPI, cors_config: CorsConfig | None) -> None:
    """Add common middleware stack in correct order.

    Middleware runs in reverse registration order, so we register:
    1. ContentTypeFixMiddleware (outermost - fixes text/plain → application/json)
    2. CORSMiddleware (handles preflight early)
    3. CorrelationIdMiddleware (adds request ID)
    4. StructLogMiddleware (innermost - logs with correlation ID)

    Args:
        app: FastAPI application instance
        cors_config: CORS configuration dict or None for defaults
    """
    app.add_middleware(StructLogMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    _add_cors_middleware(app, cors_config)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ContentTypeFixMiddleware)
    # Outermost: reject oversized bodies before anything parses them.
    app.add_middleware(RequestSizeLimitMiddleware)


def _include_core_routers(app: FastAPI) -> None:
    """Include all core API routers with auth dependency.

    Routers are included in consistent order:
    1. Health (no auth)
    2. Assistants (with auth)
    3. Threads (with auth)
    4. Runs (with auth)
    5. Stateless Runs (with auth)
    6. Crons (with auth)
    7. Store (with auth)

    Args:
        app: FastAPI application instance
    """
    # Rate limits: runs/streaming endpoints hold LLM connections open and get
    # the tighter tier; everything else gets the generous default. Health and
    # probes are exempt. Disabled unless RATE_LIMIT_ENABLED=true.
    from agent_server.auth.rate_limit import rate_limit_default, rate_limit_runs

    app.include_router(health_router)
    app.include_router(assistants_router, dependencies=[Depends(rate_limit_default)])
    app.include_router(threads_router, dependencies=[Depends(rate_limit_default)])
    app.include_router(runs_router, dependencies=[Depends(rate_limit_runs)])
    app.include_router(stateless_runs_router, dependencies=[Depends(rate_limit_runs)])
    app.include_router(crons_router, dependencies=[Depends(rate_limit_default)])
    app.include_router(store_router, dependencies=[Depends(rate_limit_default)])
    app.include_router(event_streaming_router, dependencies=[Depends(rate_limit_runs)])
    app.include_router(audit_router, dependencies=[Depends(rate_limit_default)])
    if settings.app.ENV_MODE == "LOCAL":
        # Debug probe reveals internal topology — local/dev only.
        app.include_router(mcp_router)

    # Attach @auth.on dispatch from the route registry. Routes must opt out
    # explicitly; forgetting the in-body call no longer disables authorization.
    apply_auth_enforcement(app)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    http_config: HttpConfig | None = load_http_config()
    cors_config: CorsConfig | None = http_config.get("cors") if http_config else None

    # Try to load custom app if configured
    user_app = None
    if http_config and http_config.get("app"):
        try:
            config_dir = get_config_dir()
            # Custom apps may import user packages (graphs, shared libs);
            # set up the config's dependency paths before importing.
            if config_dir:
                full_config = load_config() or {}
                add_dependency_paths(full_config.get("dependencies", []), config_dir)
            user_app = load_custom_app(http_config["app"], base_dir=config_dir)
            logger.info("Custom app loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load custom app: {e}", exc_info=True)
            raise

    if user_app:
        if not isinstance(user_app, FastAPI):
            raise TypeError(
                "Custom apps must be FastAPI applications. Use: from fastapi import FastAPI; app = FastAPI()"
            )

        application = user_app
        if http_config and http_config.get("enable_custom_route_auth", False):
            _apply_auth_to_custom_routes(application, CUSTOM_ROUTE_AUTH)
        if not application.openapi_tags:
            application.openapi_tags = OPENAPI_TAGS
        _include_core_routers(application)

        # Add root endpoint if not already defined
        if not any(route.path == "/" for route in application.routes if hasattr(route, "path")):
            application.get("/")(root_handler)

        application = merge_lifespans(application, lifespan)
        application = merge_exception_handlers(application, exception_handlers)
        _add_common_middleware(application, cors_config)
    else:
        application = FastAPI(
            swagger_ui_parameters={"tryItOutEnabled": True},
            title=settings.app.PROJECT_NAME,
            description="Production-ready Agent Protocol server",
            version=settings.app.VERSION,
            debug=settings.app.DEBUG,
            docs_url="/docs",
            redoc_url="/redoc",
            lifespan=lifespan,
            openapi_tags=OPENAPI_TAGS,
        )

        _add_common_middleware(application, cors_config)
        _include_core_routers(application)

        for exc_type, handler in exception_handlers.items():
            application.exception_handler(exc_type)(handler)

        application.get("/")(root_handler)

    setup_prometheus_metrics(application)

    return application


# Create application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(settings.app.PORT)
    uvicorn.run(app, host=settings.app.HOST, port=port)  # nosec B104 - binding to all interfaces is intentional
