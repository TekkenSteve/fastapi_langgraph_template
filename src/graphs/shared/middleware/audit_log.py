"""Audit logging middleware: records every tool call with duration and status.

Attach it to any composed agent via ``middleware=[AuditLogMiddleware()]``.
Real deployments would ship these records to an audit sink; here we log.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

logger = structlog.getLogger(__name__)


class AuditLogMiddleware(AgentMiddleware):
    """Log every tool call: name, duration, error/success."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        start = time.monotonic()
        try:
            result = await handler(request)
        except Exception as exc:
            logger.warning(
                "tool_call",
                tool=request.tool_call["name"],
                status="error",
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            raise
        logger.info(
            "tool_call",
            tool=request.tool_call["name"],
            status="ok",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        return result
