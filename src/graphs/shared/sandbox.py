"""Sandbox backend factory: the skill-script execution bridge.

User-tier skills follow the full Agent Skills spec, scripts included — but
scripts are inert unless a graph's backend can execute them. This factory is
the tier gate, wired by graph authors (see research_agent):

- unset (default): no sandbox — scratch and user skills stay in the run's
  ephemeral state, no execute tool exists, scripts are inert text.
- ``local``: LocalShellBackend — executes ON THE POD. Dev/demo only: no
  isolation, user scripts run with the server's identity. Never in prod.
- ``monty``: pydantic's Rust Python interpreter, in-process subprocess workers
  (graphs/shared/monty_sandbox.py) — Python-only, deny-by-default capabilities,
  microsecond startup. The lightweight default tier for user scripts.
- ``daytona`` / ``e2b``: a real sandboxed remote backend, user scripts execute
  off-pod. NOTE: langchain-daytona / langchain-e2b currently pin
  ``deepagents<0.7`` — install them in your deployment image at your own
  version risk (or add a BaseSandbox subclass against their SDKs directly)
  until the partner packages catch up with deepagents 0.7+.

One instance per graph build; per-request factories get a fresh sandbox per
request (isolation between runs and users).
"""

import structlog
from deepagents.backends.protocol import SandboxBackendProtocol

logger = structlog.getLogger(__name__)


def make_sandbox_backend(provider: str) -> SandboxBackendProtocol | None:
    """Build the sandbox backend for ``provider``; None means execution off."""
    if not provider or provider == "none":
        return None
    if provider == "local":
        # Dev/demo only — executes on the pod with the server's identity.
        logger.warning("sandbox_local_provider_selected", note="no isolation; never use in prod")
        from deepagents.backends.local_shell import LocalShellBackend

        return LocalShellBackend()
    if provider == "monty":
        from shared.monty_sandbox import make_monty_backend

        return make_monty_backend()
    if provider == "daytona":
        try:
            from langchain_daytona import DaytonaSandbox  # ty: ignore[unresolved-import]  # optional extra
        except ImportError as e:
            raise RuntimeError("SANDBOX_PROVIDER=daytona needs `uv sync --extra sandbox`") from e
        return DaytonaSandbox()
    if provider == "e2b":
        try:
            from langchain_e2b import E2BSandbox  # ty: ignore[unresolved-import]  # optional extra
        except ImportError as e:
            raise RuntimeError("SANDBOX_PROVIDER=e2b needs `uv sync --extra sandbox`") from e
        return E2BSandbox()
    raise ValueError(f"Unknown SANDBOX_PROVIDER: {provider!r} (expected none|local|monty|daytona|e2b)")
