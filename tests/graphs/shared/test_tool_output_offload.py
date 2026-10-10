"""Unit tests for the tool-output offload middleware."""

from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from shared.middleware.tool_output_offload import ToolOutputOffloadMiddleware


def _request() -> Any:
    return type("Req", (), {"tool_call": {"name": "web_search", "args": {}}})()


def _handler(content: Any, *, artifact: Any = None, name: str = "web_search") -> Any:
    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content, tool_call_id="1", name=name, artifact=artifact)

    return handler


async def test_small_output_is_untouched() -> None:
    middleware = ToolOutputOffloadMiddleware(max_chars=100)

    result = await middleware.awrap_tool_call(_request(), _handler("short"))

    assert isinstance(result, ToolMessage)
    assert result.content == "short"
    assert result.artifact is None


async def test_oversized_output_moves_to_the_artifact() -> None:
    middleware = ToolOutputOffloadMiddleware(max_chars=100)
    payload = "x" * 101

    result = await middleware.awrap_tool_call(_request(), _handler(payload))

    assert isinstance(result, ToolMessage)
    assert result.content != payload
    assert "101 chars" in result.content
    assert result.artifact == {"offloaded": True, "chars": 101, "content": payload}


async def test_structured_output_is_measured_and_preserved() -> None:
    """MCP tools return content blocks, not just text."""
    middleware = ToolOutputOffloadMiddleware(max_chars=50)
    blocks = [{"type": "text", "text": "y" * 100}]

    result = await middleware.awrap_tool_call(_request(), _handler(blocks))

    assert isinstance(result, ToolMessage)
    assert not isinstance(result.content, list)
    assert result.artifact["content"] == blocks


async def test_a_tool_that_already_split_its_output_is_left_alone() -> None:
    """The tool author arranged that split; rewriting it would fight them."""
    middleware = ToolOutputOffloadMiddleware(max_chars=10)
    payload = "z" * 500

    result = await middleware.awrap_tool_call(_request(), _handler(payload, artifact={"rows": 1}))

    assert isinstance(result, ToolMessage)
    assert result.content == payload
    assert result.artifact == {"rows": 1}


async def test_zero_disables_the_policy() -> None:
    middleware = ToolOutputOffloadMiddleware(max_chars=0)
    payload = "w" * 10_000

    result = await middleware.awrap_tool_call(_request(), _handler(payload))

    assert isinstance(result, ToolMessage)
    assert result.content == payload


async def test_a_command_result_passes_through() -> None:
    """Commands steer the graph; they are not tool output."""
    middleware = ToolOutputOffloadMiddleware(max_chars=1)

    async def handler(_request: Any) -> Command[Any]:
        return Command(goto="model")

    result = await middleware.awrap_tool_call(_request(), handler)

    assert isinstance(result, Command)
