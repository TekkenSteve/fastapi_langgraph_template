"""Answer policy questions, grounded in backend policy excerpts."""

from collections.abc import Callable, Coroutine
from typing import Any

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from shared.models import load_chat_model_with_fallbacks
from shop.backends import ShopBackend
from shopping_agent.prompts import POLICY_SYSTEM_PROMPT
from shopping_agent.state import Context, State


def make_policy_node(
    backend: ShopBackend,
) -> Callable[[State, Runtime[Context]], Coroutine[Any, Any, dict[str, Any]]]:
    async def answer_policy(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
        query = state.messages[-1].content if state.messages else ""
        policies = await backend.search_policies(str(query))
        system = POLICY_SYSTEM_PROMPT.format(policies="\n".join(f"- {p}" for p in policies))
        model = load_chat_model_with_fallbacks(runtime.context.model, runtime.context.fallback_models)
        response = await model.ainvoke([SystemMessage(system), *state.messages])
        return {"messages": [response]}

    return answer_policy
