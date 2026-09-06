"""Fallback free-chat node."""

from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from shared.models import load_chat_model_with_fallbacks
from shopping_agent.prompts import CHAT_SYSTEM_PROMPT
from shopping_agent.state import Context, State


async def chat(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    model = load_chat_model_with_fallbacks(runtime.context.model, runtime.context.fallback_models)
    system = CHAT_SYSTEM_PROMPT.format(system_time=datetime.now(tz=UTC).isoformat())
    response = await model.ainvoke([SystemMessage(system), *state.messages])
    return {"messages": [response]}
