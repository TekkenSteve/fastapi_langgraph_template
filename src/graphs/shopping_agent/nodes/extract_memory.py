"""Post-turn preference extraction (opt-in via Context.enable_memory_extraction).

Reads the last user/assistant text exchange only — never tool results — and
saves what looks like a durable preference through the shared write filter.
"""

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from shared.memory import MemoryStore, MemoryWriteRejected
from shared.models import load_chat_model
from shopping_agent.prompts import MEMORY_EXTRACTION_PROMPT
from shopping_agent.state import Context, State


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
    if not runtime.context.enable_memory_extraction or runtime.store is None:
        return {}
    transcript = _last_exchange_text(state)
    if not transcript:
        return {}

    model = load_chat_model(runtime.context.model)
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
