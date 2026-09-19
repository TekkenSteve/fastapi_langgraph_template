"""Graph assembly. Wiring only — no business logic lives here.

Topology: START → merchant ⇄ tools → END
"""

from typing import Any, cast

from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode

from merchant_agent.edges import route_after_merchant
from merchant_agent.nodes import make_merchant_node
from merchant_agent.state import Context, InputState, State
from merchant_agent.tools import make_merchant_tools
from shop.backends import MerchantBackend


def build_merchant_agent(backend: MerchantBackend) -> StateGraph[State, Context, InputState, State]:
    tools = make_merchant_tools(backend)

    builder = StateGraph(State, input_schema=InputState, context_schema=Context)
    # langgraph's _Node union doesn't cover (State, Runtime[Context]) callables;
    # the node is a valid node at runtime — boundary cast.
    builder.add_node("merchant", cast("Any", make_merchant_node(tools)))
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "merchant")
    builder.add_conditional_edges("merchant", route_after_merchant)
    builder.add_edge("tools", "merchant")

    return builder
