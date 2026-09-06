"""Classify the latest user message into a routing intent."""

from typing import Any, Literal

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

from shared.models import load_chat_model_with_fallbacks
from shopping_agent.prompts import CLASSIFY_PROMPT
from shopping_agent.state import Context, State


class IntentResult(BaseModel):
    intent: Literal["shop", "policy", "order", "chat"]


async def classify(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    classifier = load_chat_model_with_fallbacks(
        runtime.context.model, runtime.context.fallback_models
    ).with_structured_output(IntentResult)
    result = await classifier.ainvoke([SystemMessage(CLASSIFY_PROMPT), *state.messages])
    return {"intent": result.intent}
