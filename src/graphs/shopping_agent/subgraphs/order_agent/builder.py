"""Order-tracking sub-agent assembly."""

from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from shop.backends import ShopBackend
from shopping_agent.state import Context
from shopping_agent.subgraphs.order_agent.nodes import answer_orders, make_fetch_orders_node
from shopping_agent.subgraphs.order_agent.state import OrderState


def build_order_agent(backend: ShopBackend) -> StateGraph[OrderState, Context, OrderState, OrderState]:
    builder = StateGraph(OrderState, context_schema=Context)
    builder.add_node("fetch_orders", cast("Any", make_fetch_orders_node(backend)))
    builder.add_node("answer", cast("Any", answer_orders))

    builder.add_edge(START, "fetch_orders")
    builder.add_edge("fetch_orders", "answer")
    builder.add_edge("answer", END)

    return builder
