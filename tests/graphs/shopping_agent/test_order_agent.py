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


async def test_answer_emits_order_status_card() -> None:
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage, HumanMessage

    import shopping_agent.subgraphs.order_agent.nodes as nodes_mod
    from shopping_agent.state import Context

    class _FakeModel:
        async def ainvoke(self, messages):
            return AIMessage(content="Your order is on its way.")

    nodes_mod.load_chat_model = lambda name: _FakeModel()
    state = OrderState(
        messages=[HumanMessage(content="where is my order?")],
        orders=[
            {"order_id": "order-0001", "status": "processing", "lines": [{"product_id": "kettle-01", "quantity": 1}]}
        ],
    )
    result = await nodes_mod.answer_orders(state, SimpleNamespace(context=Context()))
    assert result["presentations"][0]["component"] == "OrderStatusCard"
    assert result["presentations"][0]["payload"]["order"]["order_id"] == "order-0001"
