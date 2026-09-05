"""Load the customer's long-term preferences from the server store."""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from shopping_agent.state import Context, State


async def initialize(state: State, config: RunnableConfig, runtime: Runtime[Context]) -> dict[str, Any]:
    """Read preferences written by the remember_preference tool in past sessions."""
    store = runtime.store
    if store is None:
        return {}
    user_id = config.get("configurable", {}).get("user_id", "demo-user")
    item = await store.aget(("preferences",), user_id)
    return {"preferences": dict(item.value) if item else {}}
