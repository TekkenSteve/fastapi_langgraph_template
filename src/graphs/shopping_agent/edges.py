"""Routing functions: read state, return the next node name.

Pure decision logic only — no LLM calls, no IO. This is the most valuable
code to unit-test in any graph.
"""

from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.graph import END

from shopping_agent.state import State

_INTENT_TO_NODE = {"shop": "shop", "policy": "policy", "order": "order_agent", "chat": "chat"}


def route_after_classify(state: State) -> Literal["shop", "policy", "order_agent", "chat"]:
    return _INTENT_TO_NODE[state.intent]


def route_after_shop(state: State) -> Literal["tools", "__end__"]:
    last = state.messages[-1]
    if not isinstance(last, AIMessage):
        raise ValueError(f"Expected AIMessage in output edges, but got {type(last).__name__}")
    if last.tool_calls:
        return "tools"
    return END
