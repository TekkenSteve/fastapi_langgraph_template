"""Optional Prometheus metrics via prometheus-fastapi-instrumentator.

Controlled by ``ENABLE_PROMETHEUS_METRICS`` env var (default: false).
When enabled, exposes a ``/metrics`` endpoint with standard HTTP and
Python runtime metrics in Prometheus exposition format.
"""

import prometheus_client
import structlog
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from agent_server.config.settings import settings

logger = structlog.getLogger(__name__)

REAPER_RECOVERED_RUNS = prometheus_client.Counter(
    "agent_server_reaper_recovered_runs_total",
    "Runs recovered by the lease reaper, by outcome: crashed_retried "
    "(expired lease, re-enqueued), crashed_exhausted (max retries exceeded, "
    "marked failed), stuck_pending (missing from the queue, re-enqueued). Counts only "
    "confirmed Redis pushes and DB updates; recovery that falls back to the "
    "workers' Postgres poll during a Redis outage is not counted.",
    labelnames=["outcome"],
)

# Pre-create label children so every outcome series renders as 0 on /metrics
# before the first recovery event (absent series break rate() alerts).
for _outcome in ("crashed_retried", "crashed_exhausted", "stuck_pending"):
    REAPER_RECOVERED_RUNS.labels(outcome=_outcome)

THREAD_TTL_SWEPT = prometheus_client.Counter(
    "agent_server_thread_ttl_swept_threads_total",
    "Threads processed by the TTL sweep or POST /threads/prune, by outcome: "
    "deleted (strategy=delete, thread removed), pruned (strategy=keep_latest, "
    "history compacted), error (item failed and will be retried next tick).",
    labelnames=["outcome"],
)

for _outcome in ("deleted", "pruned", "error"):
    THREAD_TTL_SWEPT.labels(outcome=_outcome)


# --- circuit breakers -----------------------------------------------------------
# A flapping upstream is invisible in HTTP metrics: the server degrades by
# dropping features, not by returning 5xx. These two make it alertable.
CIRCUIT_BREAKER_OPEN = prometheus_client.Gauge(
    "agent_server_circuit_breaker_open",
    "1 while the breaker for this endpoint is open (calls fast-fail), 0 otherwise. "
    "One series per registered breaker label; created at 0 so temporary abscence "
    "cannot be mistaken for a closed circuit.",
    labelnames=["name"],
)

CIRCUIT_BREAKER_TRIPS = prometheus_client.Counter(
    "agent_server_circuit_breaker_trips_total",
    "Times the breaker opened: reaching the failure threshold, or a half-open probe failing and re-opening.",
    labelnames=["name"],
)

# --- MCP tool loading -----------------------------------------------------------
MCP_TOOL_LOADS = prometheus_client.Counter(
    "agent_server_mcp_tool_loads_total",
    "MCP tool loads per server, by outcome. 'ok' is a completed handshake; every "
    "other value is the classified failure reason (mcp_errors.McpFailureReason), "
    "so an auth problem and a dead server are distinguishable on a dashboard.",
    labelnames=["server", "outcome"],
)

MCP_TOOLS_EXPOSED = prometheus_client.Gauge(
    "agent_server_mcp_tools_exposed",
    "Tools the server exposed after allowlist and model-facing filtering. A drop "
    "to 0 on a previously healthy server means the catalog disappeared (auth "
    "expiry, a renamed tool set, or an allowlist that matches nothing).",
    labelnames=["server"],
)


def setup_prometheus_metrics(
    app: FastAPI,
    registry: prometheus_client.CollectorRegistry | None = None,
) -> None:
    """Conditionally attach Prometheus instrumentator to the app.

    No-op when ``ENABLE_PROMETHEUS_METRICS`` is false.

    Args:
        app: FastAPI application instance.
        registry: Optional Prometheus collector registry. When provided, metrics
            are collected into this registry instead of the global default.
            Primarily useful in tests to avoid cross-test pollution.

    Note:
        The ``/metrics`` endpoint is **not** protected by the server's authentication
        middleware. This is intentional — Prometheus scrapers typically do not
        support application-level auth. If the endpoint must be restricted, use
        network-level controls (firewall rules, internal load-balancer, etc.).
    """
    if not settings.observability.ENABLE_PROMETHEUS_METRICS:
        return

    instrumentator = Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/ready", "/live", "/info", "/metrics", "/docs", "/redoc", "/openapi.json"],
        registry=registry,
    )
    instrumentator.instrument(app)
    instrumentator.expose(app, endpoint="/metrics", include_in_schema=False)
    logger.info("Prometheus metrics enabled at /metrics")
