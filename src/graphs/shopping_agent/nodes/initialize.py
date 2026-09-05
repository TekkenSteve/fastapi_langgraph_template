"""Load the customer's long-term preferences from the server store."""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from shared.memory import MemoryStore
from shopping_agent.state import Context, State


async def initialize(state: State, config: RunnableConfig, runtime: Runtime[Context]) -> dict[str, Any]:
    """Read preferences saved in past sessions (retention-filtered)."""
    store = runtime.store
    if store is None:
        return {}
    user_id = config.get("configurable", {}).get("user_id", "demo-user")
    preferences = await MemoryStore(store).load(user_id)
    return {"preferences": preferences}
