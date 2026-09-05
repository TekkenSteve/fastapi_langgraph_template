"""Unit tests for the order_agent subgraph."""

from shop.backends import FakeShopBackend
from shopping_agent.subgraphs.order_agent import build_order_agent
from shopping_agent.subgraphs.order_agent.nodes import make_fetch_orders_node
from shopping_agent.subgraphs.order_agent.state import OrderState

CONFIG = {"configurable": {"user_id": "u1"}}


async def test_fetch_orders_returns_user_orders() -> None:
    backend = FakeShopBackend()
    await backend.add_to_cart("u1", "kettle-01", 1)
    await backend.checkout("u1")

    node = make_fetch_orders_node(backend)
    result = await node(OrderState(), CONFIG)

    assert result["orders"][0]["status"] == "processing"
    assert result["orders"][0]["lines"][0]["product_id"] == "kettle-01"


async def test_fetch_orders_empty_for_new_user() -> None:
    node = make_fetch_orders_node(FakeShopBackend())
    result = await node(OrderState(), CONFIG)
    assert result["orders"] == []


def test_order_agent_compiles() -> None:
    graph = build_order_agent(FakeShopBackend()).compile()
    assert set(graph.get_graph().nodes) >= {"fetch_orders", "answer"}
