"""Move oversized tool output out of the model's context.

A tool that dumps a log file, a search page or a whole table into its
``ToolMessage`` spends the model's context on data nobody reads twice — and the
richer the payload (the MCP Apps case especially), the worse it gets. This
middleware bounds that: when a tool result exceeds the configured size **and
carries no artifact of its own**, the full payload moves to
``ToolMessage.artifact`` — which the UI reads and the model never sees — and the
model gets a short stub saying so.

Two rules keep it predictable:

- a result that already has an artifact is left alone: a ``content_and_artifact``
  tool has arranged its own split, and rewriting it would fight the tool author;
- ``max_chars=0`` disables the policy.

Like ``audit_log``, this is *server* middleware: the application layer registers
it (``src/http_app.py``) and passes the limit, so no graph has to opt in and the
policy module needs no framework import.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

logger = structlog.getLogger(__name__)

_STUB = (
    "[tool output omitted from context: {chars} chars, over the {limit}-char limit. "
    "The full payload is attached to this message's artifact for the UI. "
    "Re-run the tool with narrower arguments to read it in context.]"
)


def _size(content: Any) -> int:
    """Characters the model would actually pay for."""
    if isinstance(content, str):
        return len(content)
    try:
        return len(json.dumps(content, default=str))
    except (TypeError, ValueError):
        return len(str(content))


class ToolOutputOffloadMiddleware(AgentMiddleware):
    """Offload oversized tool output to the message artifact."""

    def __init__(self, *, max_chars: int = 50_000) -> None:
        super().__init__()
        self._max_chars = max_chars

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        if not self._max_chars or not isinstance(result, ToolMessage) or result.artifact is not None:
            return result

        chars = _size(result.content)
        if chars <= self._max_chars:
            return result

        logger.info("tool_output_offloaded", tool=result.name, chars=chars, limit=self._max_chars)
        return result.model_copy(
            update={
                "content": _STUB.format(chars=chars, limit=self._max_chars),
                "artifact": {"offloaded": True, "chars": chars, "content": result.content},
            }
        )
