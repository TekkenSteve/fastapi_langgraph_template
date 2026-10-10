"""Audit logging middleware: records every tool call with duration and status.

Always writes a structured log line; optionally hands the same event to an
injected **writer** (the server wires one that appends to the audit ledger,
``agent_server.repo.audit``). The split is deliberate: this module owns *when*
and *what*, the writer owns *where*, and the middleware stays free of framework
imports. A writer that raises is logged and swallowed — an audit sink outage must
not fail the run it is describing.

Events cross the writer boundary as plain dicts, matching how graph-side code
receives hub data (``hub/queries.py``): neither side imports the other's models.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import structlog
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

logger = structlog.getLogger(__name__)

# Arguments are one line in the ledger, never the payload archive.
_MAX_ARGS_CHARS = 500


class AuditWriter(Protocol):
    """Where recorded events go (the server injects a ledger writer)."""

    async def __call__(self, event: dict[str, Any]) -> None: ...


def _invocation_context(request: ToolCallRequest) -> dict[str, Any]:
    """The run identity a tool call happens under, read from the run config.

    ``runtime.config`` carries what ``inject_user_context`` put there, so the
    ledger can answer "whose agent did this" without trusting the tool.
    """
    config = getattr(getattr(request, "runtime", None), "config", None) or {}
    configurable = config.get("configurable") or {}
    if not isinstance(configurable, dict):
        return {}
    return {
        "user_id": configurable.get("user_id"),
        "thread_id": configurable.get("thread_id"),
        "run_id": configurable.get("run_id"),
    }


class AuditLogMiddleware(AgentMiddleware):
    """Record every tool call: name, duration, error/success."""

    def __init__(self, *, writer: AuditWriter | None = None) -> None:
        super().__init__()
        self._writer = writer

    async def _record(self, event: dict[str, Any]) -> None:
        if self._writer is None:
            return
        try:
            await self._writer(event)
        except Exception as e:  # an audit sink outage must never fail the run
            logger.warning("audit_writer_failed", action=event.get("action"), error=str(e))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool = request.tool_call["name"]
        context = _invocation_context(request)
        start = time.monotonic()
        try:
            result = await handler(request)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.warning("tool_call", tool=tool, status="error", error=str(exc), duration_ms=duration_ms)
            await self._record(
                {
                    **context,
                    "action": "tool_call",
                    "resource": tool,
                    "status": "error",
                    "duration_ms": duration_ms,
                    "detail": str(exc)[:_MAX_ARGS_CHARS],
                }
            )
            raise
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("tool_call", tool=tool, status="ok", duration_ms=duration_ms)
        args = str(request.tool_call.get("args"))[:_MAX_ARGS_CHARS]
        await self._record(
            {
                **context,
                "action": "tool_call",
                "resource": tool,
                "status": "ok",
                "duration_ms": duration_ms,
                "detail": f"args={args}",
            }
        )
        return result
