"""Node functions. Factories close over what a node needs."""

import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from pydantic import BaseModel

from shared.memory import MemoryStore, MemoryWriteRejected
from shared.models import load_chat_model_with_fallbacks
from shop.backends import ShopBackend
from shopping_agent.prompts import (
    CHAT_SYSTEM_PROMPT,
    CLASSIFY_PROMPT,
    MEMORY_EXTRACTION_PROMPT,
    POLICY_SYSTEM_PROMPT,
    SHOP_SYSTEM_PROMPT,
)
from shopping_agent.state import Context, State

__all__ = ["initialize", "classify", "make_shop_node", "make_policy_node", "chat", "extract_memory"]


async def initialize(state: State, config: RunnableConfig, runtime: Runtime[Context]) -> dict[str, Any]:
    """Read preferences saved in past sessions (retention-filtered)."""
    store = runtime.store
    if store is None:
        return {}
    user_id = config.get("configurable", {}).get("user_id", "demo-user")
    preferences = await MemoryStore(store).load(user_id)
    return {"preferences": preferences}


class IntentResult(BaseModel):
    intent: Literal["shop", "policy", "order", "chat"]


async def classify(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    """Classify the latest user message into a routing intent."""
    classifier = load_chat_model_with_fallbacks(
        runtime.context.model, runtime.context.fallback_models
    ).with_structured_output(IntentResult)
    result = await classifier.ainvoke([SystemMessage(CLASSIFY_PROMPT), *state.messages])
    return {"intent": result.intent}


def make_shop_node(tools: list) -> Callable[[State, Runtime[Context]], Coroutine[Any, Any, dict[str, Any]]]:
    """The shopping node: model call with shop tools bound (ReAct loop)."""

    async def shop(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
        model = load_chat_model_with_fallbacks(runtime.context.model, runtime.context.fallback_models).bind_tools(tools)
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


def make_policy_node(
    backend: ShopBackend,
) -> Callable[[State, Runtime[Context]], Coroutine[Any, Any, dict[str, Any]]]:
    """Answer policy questions, grounded in backend policy excerpts."""

    async def answer_policy(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
        query = state.messages[-1].content if state.messages else ""
        policies = await backend.search_policies(str(query))
        system = POLICY_SYSTEM_PROMPT.format(policies="\n".join(f"- {p}" for p in policies))
        model = load_chat_model_with_fallbacks(runtime.context.model, runtime.context.fallback_models)
        response = await model.ainvoke([SystemMessage(system), *state.messages])
        return {"messages": [response]}

    return answer_policy


async def chat(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    """Fallback free-chat node."""
    model = load_chat_model_with_fallbacks(runtime.context.model, runtime.context.fallback_models)
    system = CHAT_SYSTEM_PROMPT.format(system_time=datetime.now(tz=UTC).isoformat())
    response = await model.ainvoke([SystemMessage(system), *state.messages])
    return {"messages": [response]}


def _last_exchange_text(state: State) -> str | None:
    """The most recent user message + assistant reply, text only."""
    user_text = assistant_text = None
    for message in reversed(state.messages):
        if assistant_text is None and isinstance(message, AIMessage) and not message.tool_calls:
            assistant_text = message.content if isinstance(message.content, str) else None
        elif user_text is None and isinstance(message, HumanMessage):
            user_text = message.content if isinstance(message.content, str) else None
        if user_text is not None and assistant_text is not None:
            break
    if not user_text:
        return None
    return f"user: {user_text}\nassistant: {assistant_text or ''}"


async def extract_memory(state: State, config: RunnableConfig, runtime: Runtime[Context]) -> dict[str, Any]:
    """Post-turn preference extraction (opt-in via Context.enable_memory_extraction).

    Reads the last user/assistant text exchange only — never tool results — and
    saves what looks like a durable preference through the shared write filter.
    """
    if not runtime.context.enable_memory_extraction or runtime.store is None:
        return {}
    transcript = _last_exchange_text(state)
    if not transcript:
        return {}

    model = load_chat_model_with_fallbacks(runtime.context.model, runtime.context.fallback_models)
    response = await model.ainvoke([SystemMessage(MEMORY_EXTRACTION_PROMPT), HumanMessage(transcript)])
    text = response.content if isinstance(response.content, str) else ""
    try:
        candidates = json.loads(text)
    except json.JSONDecodeError:
        return {}  # model answered prose; nothing to save
    if not isinstance(candidates, list):
        return {}

    memory = MemoryStore(runtime.store)
    user_id = config.get("configurable", {}).get("user_id", "demo-user")
    for candidate in candidates:
        if not isinstance(candidate, dict) or "key" not in candidate or "value" not in candidate:
            continue
        try:
            await memory.save(user_id, str(candidate["key"]), str(candidate["value"]))
        except MemoryWriteRejected:
            continue  # the write filter refused it; that is the filter working
    return {}
