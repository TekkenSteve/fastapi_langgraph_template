"""What this deployment can actually do — checked, not assumed.

Borrowed from Octop's capability gate: a flag in ``.env`` is a *request*, not a
fact. "Tool authorization is on" with no policy backend configured means every
tool call is denied; "scripts run in a sandbox" with the sandbox package absent
means the first script fails. Both are half-ready states that a caller should be
able to see *before* it depends on them, so this module answers with a check
rather than with the flag.

The report is read-only and carries no secrets — it is served on ``/info``, which
is unauthenticated by design (a UI needs to know what to hide).
"""

import importlib.util
from typing import Any

from agent_server.config.graph_config import load_auth_config
from agent_server.config.settings import settings

# Optional sandbox tiers and the package each one needs at import time.
_SANDBOX_PACKAGES: dict[str, str] = {
    "monty": "pydantic_monty",
    "daytona": "langchain_daytona",
    "e2b": "langchain_e2b",
}


def _installed(module: str) -> bool:
    """Whether *module* can be imported, without importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # a broken parent package is "not installed"
        return False


def _capability(ready: bool, detail: str) -> dict[str, Any]:
    return {"ready": ready, "detail": detail}


def sandbox_capability() -> dict[str, Any]:
    """The skill-script execution tier and whether it can really run."""
    provider = (settings.sandbox.SANDBOX_PROVIDER or "").strip().lower()
    if provider in ("", "none"):
        return _capability(False, "off — user skill scripts stay inert text")
    if provider == "local":
        return _capability(True, "local — executes on the pod with no isolation (dev only)")
    package = _SANDBOX_PACKAGES.get(provider)
    if package is None:
        return _capability(False, f"unknown SANDBOX_PROVIDER {provider!r}")
    if not _installed(package):
        return _capability(False, f"{provider} selected but {package} is not installed")
    return _capability(True, provider)


def tool_authorization_capability() -> dict[str, Any]:
    """Per-invocation MCP tool authorization, and whether a backend backs it."""
    if not settings.mcp.MCP_TOOL_AUTHZ_ENABLED:
        return _capability(False, "off")
    backend = "opa" if settings.policy.OPA_URL else "local"
    if backend == "local":
        # Deliberate fail-closed design: the local engine denies ownerless tool
        # resources, so enabling this without an external engine denies every
        # tool call. Report it as not ready rather than as a working feature.
        return _capability(False, "enabled, but only the local policy engine is configured (all tool calls deny)")
    return _capability(True, backend)


def authentication_capability() -> dict[str, Any]:
    """Whether requests carry an authenticated identity at all."""
    auth_type = settings.app.AUTH_TYPE
    handler = load_auth_config()
    if auth_type == "noop":
        return _capability(False, "noop — every request is anonymous")
    if handler is None:
        # The middleware falls back to noop when no handler module is
        # configured, whatever AUTH_TYPE says.
        return _capability(False, f"{auth_type} configured but no auth.path handler is set (falls back to noop)")
    return _capability(True, auth_type)


def build_capability_report() -> dict[str, dict[str, Any]]:
    """The full report, keyed by capability name."""
    return {
        "authentication": authentication_capability(),
        "sandbox": sandbox_capability(),
        "tool_authorization": tool_authorization_capability(),
        "mcp_apps": _capability(settings.mcp.MCP_APPS_ENABLED, "host proxy routes are always mounted"),
        "audit_ledger": _capability(settings.agent.AUDIT_LOG_ENABLED, "audit_log table"),
        "rate_limit": _capability(settings.app.RATE_LIMIT_ENABLED, settings.app.RATE_LIMIT_DEFAULT),
        "multi_instance": _capability(settings.redis.REDIS_BROKER_ENABLED, "redis broker and workers"),
    }
