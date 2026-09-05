"""Unit tests for shopping_agent edges (pure routing functions)."""

from langchain_core.messages import AIMessage, HumanMessage

from shopping_agent.edges import route_after_classify, route_after_shop
from shopping_agent.state import State


def test_route_after_classify_returns_intent() -> None:
    state = State(messages=[HumanMessage(content="hi")], intent="shop")
    assert route_after_classify(state) == "shop"

    state = State(messages=[HumanMessage(content="hi")], intent="policy")
    assert route_after_classify(state) == "policy"


def test_route_after_shop_goes_to_tools_when_tool_calls() -> None:
    ai = AIMessage(content="", tool_calls=[{"name": "search_products", "args": {"query": "kettle"}, "id": "1"}])
    state = State(messages=[HumanMessage(content="find a kettle"), ai])
    assert route_after_shop(state) == "tools"


def test_route_after_shop_ends_without_tool_calls() -> None:
    state = State(messages=[HumanMessage(content="hi"), AIMessage(content="Hello!")])
    assert route_after_shop(state) == "__end__"
