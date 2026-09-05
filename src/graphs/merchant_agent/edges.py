"""Routing functions: read state, return the next node name. Pure logic only."""

from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.graph import END

from merchant_agent.state import State


def route_after_merchant(state: State) -> Literal["tools", "__end__"]:
    last = state.messages[-1]
    if not isinstance(last, AIMessage):
        raise ValueError(f"Expected AIMessage in output edges, but got {type(last).__name__}")
    if last.tool_calls:
        return "tools"
    return END
