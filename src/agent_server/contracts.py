"""Public contracts for graph authors.

This is the ONLY agent_server module graphs may import — the framework's
stable API surface toward user graphs (same role langgraph_sdk.ServerRuntime
plays for LangGraph Platform).

Application-layer capability providers (the hub package's skill/connection
loaders) are imported by graphs from their own packages directly — same
pattern as graphs importing ``shop.backends``.
"""

from agent_server.config.settings import settings
from agent_server.domain.run_config import configurable_user_id
from agent_server.repo.graphs.agent_middleware import compose_middleware, server_middleware
from agent_server.repo.graphs.mcp_loader import with_mcp_tools


def sandbox_provider() -> str:
    """The deployment's skill-script sandbox tier (``SANDBOX_PROVIDER``).

    Pass it to ``shared.sandbox.make_sandbox_backend``. Read through the typed
    settings rather than ``os.environ`` so the knob is documented in one place
    and validated like every other.
    """
    return settings.sandbox.SANDBOX_PROVIDER


__all__ = [
    "compose_middleware",
    "configurable_user_id",
    "sandbox_provider",
    "server_middleware",
    "with_mcp_tools",
]
