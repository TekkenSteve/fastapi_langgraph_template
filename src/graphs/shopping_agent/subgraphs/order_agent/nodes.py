"""Order-tracking nodes: fetch recent orders, then answer with them."""

from typing import Any

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from shared.models import load_chat_model
from shop.backends import ShopBackend
from shopping_agent.state import Context
from shopping_agent.subgraphs.order_agent.state import OrderState

ANSWER_PROMPT = """You are the Acme shop assistant answering an order-status question.

The customer's recent orders:
{orders}

Answer from this data only. If the list is empty, say no orders were found.
"""


def make_fetch_orders_node(backend: ShopBackend):
    async def fetch_orders(state: OrderState, config: RunnableConfig) -> dict[str, Any]:
        user_id = config.get("configurable", {}).get("user_id", "demo-user")
        orders = await backend.get_orders(user_id)
        return {
            "orders": [
                {"order_id": o.order_id, "status": o.status, "lines": [vars(line) for line in o.lines]} for o in orders
            ]
        }

    return fetch_orders


async def answer_orders(state: OrderState, runtime: Runtime[Context]) -> dict[str, Any]:
    lines = (
        "\n".join(f"- {o['order_id']} ({o['status']}): {len(o['lines'])} item(s)" for o in state.orders) or "no orders"
    )
    model = load_chat_model(runtime.context.model)
    response = await model.ainvoke([SystemMessage(ANSWER_PROMPT.format(orders=lines)), *state.messages])
    return {"messages": [response]}
