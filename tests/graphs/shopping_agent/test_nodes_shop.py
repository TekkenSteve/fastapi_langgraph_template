"""Unit tests for the shop node (ReAct loop model call)."""

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from shopping_agent.nodes.shop import make_shop_node
from shopping_agent.state import Context, State


class _FakeModel:
    def __init__(self, response: AIMessage) -> None:
        self._response = response

    def bind_tools(self, tools: list) -> "_FakeModel":
        return self

    async def ainvoke(self, messages: list) -> AIMessage:
        return self._response


def _runtime() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(context=Context())


async def test_shop_returns_model_response(monkeypatch: pytest.MonkeyPatch) -> None:
    response = AIMessage(content="Here are some coffee makers.")
    monkeypatch.setattr(
        "shopping_agent.nodes.shop.load_chat_model_with_fallbacks", lambda name, _fallbacks=None: _FakeModel(response)
    )
    node = make_shop_node([])
    result = await node(State(messages=[HumanMessage(content="coffee?")]), _runtime())
    assert result["messages"] == [response]


async def test_shop_graceful_exit_on_last_step(monkeypatch: pytest.MonkeyPatch) -> None:
    response = AIMessage(
        content="",
        id="msg-1",
        tool_calls=[{"name": "search_products", "args": {"query": "x"}, "id": "1"}],
    )
    monkeypatch.setattr(
        "shopping_agent.nodes.shop.load_chat_model_with_fallbacks", lambda name, _fallbacks=None: _FakeModel(response)
    )
    node = make_shop_node([])
    state = State(messages=[HumanMessage(content="coffee?")], is_last_step=True)
    result = await node(state, _runtime())

    final = result["messages"][0]
    assert final.tool_calls == []
    assert final.id == "msg-1"
    assert "allowed number of steps" in final.content
