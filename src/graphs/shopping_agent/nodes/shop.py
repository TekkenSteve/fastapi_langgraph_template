"""The shopping node: model call with shop tools bound (ReAct loop)."""

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, cast

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

from shared.models import load_chat_model
from shopping_agent.prompts import SHOP_SYSTEM_PROMPT
from shopping_agent.state import Context, State


def make_shop_node(tools: list) -> Callable[[State, Runtime[Context]], Coroutine[Any, Any, dict[str, Any]]]:
    async def shop(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
        model = load_chat_model(runtime.context.model).bind_tools(tools)
        system = SHOP_SYSTEM_PROMPT.format(
            system_time=datetime.now(tz=UTC).isoformat(),
            preferences=state.preferences or "none known",
        )
        response = cast("AIMessage", await model.ainvoke([SystemMessage(system), *state.messages]))

        # Last allowed step but the model still wants tools: answer gracefully
        # instead of dying on the recursion limit.
        if state.is_last_step and response.tool_calls:
            return {
                "messages": [
                    AIMessage(
                        id=response.id,
                        content="I couldn't complete this within the allowed number of steps. "
                        "Here's what I found so far — how would you like to proceed?",
                    )
                ]
            }
        return {"messages": [response]}

    return shop
