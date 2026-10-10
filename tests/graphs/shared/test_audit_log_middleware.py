"""Unit tests for the audit-log middleware's writer seam."""

from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from shared.middleware.audit_log import AuditLogMiddleware


def _request(configurable: dict[str, Any] | None = None) -> Any:
    runtime = type("Runtime", (), {"config": {"configurable": configurable or {}}})()
    return type("Req", (), {"tool_call": {"name": "web_search", "args": {"q": "x"}}, "runtime": runtime})()


async def _handler(_request: Any) -> ToolMessage:
    return ToolMessage("ok", tool_call_id="1")


async def test_the_event_carries_the_run_identity_and_outcome() -> None:
    events: list[dict[str, Any]] = []

    async def writer(event: dict[str, Any]) -> None:
        events.append(event)

    middleware = AuditLogMiddleware(writer=writer)
    request = _request({"user_id": "u1", "thread_id": "t1", "run_id": "r1"})

    await middleware.awrap_tool_call(request, _handler)

    assert events == [
        {
            "user_id": "u1",
            "thread_id": "t1",
            "run_id": "r1",
            "action": "tool_call",
            "resource": "web_search",
            "status": "ok",
            "duration_ms": events[0]["duration_ms"],
            "detail": "args={'q': 'x'}",
        }
    ]
    assert isinstance(events[0]["duration_ms"], int)


async def test_a_failing_tool_is_recorded_and_reraised() -> None:
    events: list[dict[str, Any]] = []

    async def writer(event: dict[str, Any]) -> None:
        events.append(event)

    async def handler(_request: Any) -> ToolMessage:
        raise RuntimeError("tool exploded")

    middleware = AuditLogMiddleware(writer=writer)

    with pytest.raises(RuntimeError, match="exploded"):
        await middleware.awrap_tool_call(_request(), handler)

    assert events[0]["status"] == "error"
    assert "exploded" in events[0]["detail"]


async def test_a_writer_outage_does_not_fail_the_call() -> None:
    async def writer(_event: dict[str, Any]) -> None:
        raise ConnectionError("ledger down")

    middleware = AuditLogMiddleware(writer=writer)

    result = await middleware.awrap_tool_call(_request(), _handler)

    assert isinstance(result, ToolMessage)


async def test_without_a_writer_only_the_log_line_is_emitted(caplog: pytest.LogCaptureFixture) -> None:
    """The default keeps the middleware usable without a ledger (graph tests)."""
    middleware = AuditLogMiddleware()

    with caplog.at_level("INFO"):
        result = await middleware.awrap_tool_call(_request(), _handler)

    assert isinstance(result, ToolMessage)
