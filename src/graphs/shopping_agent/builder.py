"""Graph assembly. Wiring only — no business logic lives here.

Topology:

    START → initialize → classify ─┬─→ shop ⇄ tools ──→ extract_memory ─→ END
                                   ├─→ policy ─────────↗
                                   ├─→ order_agent ────↗
                                   └─→ chat ───────────↗
"""

from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode

from shop.backends import ShopBackend
from shopping_agent.edges import route_after_classify, route_after_shop
from shopping_agent.nodes import chat, classify, extract_memory, initialize, make_policy_node, make_shop_node
from shopping_agent.state import Context, InputState, State
from shopping_agent.subgraphs.order_agent import build_order_agent
from shopping_agent.tools import make_shop_tools


def build_shopping_agent(backend: ShopBackend) -> StateGraph:
    tools = make_shop_tools(backend)

    builder = StateGraph(State, input_schema=InputState, context_schema=Context)
    builder.add_node("initialize", initialize)
    builder.add_node("classify", classify)
    builder.add_node("shop", make_shop_node(tools))
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("policy", make_policy_node(backend))
    builder.add_node("chat", chat)
    builder.add_node("extract_memory", extract_memory)
    # A compiled subgraph is itself a node: shared channels (messages) flow
    # in and out; its private channels (orders) stay inside.
    builder.add_node("order_agent", build_order_agent(backend).compile())

    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "classify")
    builder.add_conditional_edges("classify", route_after_classify)
    builder.add_conditional_edges("shop", route_after_shop)
    builder.add_edge("tools", "shop")
    builder.add_edge("policy", "extract_memory")
    builder.add_edge("chat", "extract_memory")
    builder.add_edge("extract_memory", "__end__")
    builder.add_edge("order_agent", "extract_memory")

    return builder
