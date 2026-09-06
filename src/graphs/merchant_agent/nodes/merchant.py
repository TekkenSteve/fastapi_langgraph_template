"""The merchant node: model call with merchant tools bound (ReAct loop)."""

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, cast

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

from merchant_agent.prompts import MERCHANT_SYSTEM_PROMPT
from merchant_agent.state import Context, State
from shared.models import load_chat_model_with_fallbacks


def make_merchant_node(tools: list) -> Callable[[State, Runtime[Context]], Coroutine[Any, Any, dict[str, Any]]]:
    async def merchant(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
        model = load_chat_model_with_fallbacks(runtime.context.model, runtime.context.fallback_models).bind_tools(tools)
        system = MERCHANT_SYSTEM_PROMPT.format(system_time=datetime.now(tz=UTC).isoformat())
        response = cast("AIMessage", await model.ainvoke([SystemMessage(system), *state.messages]))

        # Last allowed step but the model still wants tools: answer gracefully.
        if state.is_last_step and response.tool_calls:
            return {
                "messages": [
                    AIMessage(
                        id=response.id,
                        content="I couldn't complete this within the allowed number of steps. "
                        "Nothing was applied — pending staged changes are unaffected.",
                    )
                ]
            }
        return {"messages": [response]}

    return merchant
