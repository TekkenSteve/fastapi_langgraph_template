"""Order-tracking nodes: fetch recent orders, then answer with them."""

from typing import Any

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from shared.models import load_chat_model_with_fallbacks
from shop.backends import ShopBackend
from shopping_agent.state import Context
from shopping_agent.subgraphs.order_agent.state import OrderState

ANSWER_PROMPT = """You are the Acme shop assistant answering an order-status question.

The customer's recent orders:
{orders}

Answer from this data only. If the list is empty, say no orders were found.
Lead with the two facts they came for (current status, expected next step),
in few words. For anything about returns, refunds, or compensation, say the
terms are with the store and never invent compensation.
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
    model = load_chat_model_with_fallbacks(runtime.context.model, runtime.context.fallback_models)
    response = await model.ainvoke([SystemMessage(ANSWER_PROMPT.format(orders=lines)), *state.messages])

    update: dict[str, Any] = {"messages": [response]}
    if state.orders:
        latest = state.orders[-1]
        update["presentations"] = [{"component": "OrderStatusCard", "payload": {"order": latest}}]
    return update
