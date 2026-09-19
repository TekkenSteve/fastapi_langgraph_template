"""Standalone smoke run: uv run python -m merchant_agent

Requires a real LLM key (e.g. OPENAI_API_KEY) in .env or the environment.
"""

import asyncio
from typing import Any, cast

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from merchant_agent.builder import build_merchant_agent
from merchant_agent.state import Context
from shop.backends import get_merchant_backend


async def main() -> None:
    graph = build_merchant_agent(get_merchant_backend()).compile(checkpointer=MemorySaver())
    config: RunnableConfig = {"configurable": {"thread_id": "smoke", "user_id": "staff-1"}}
    # InputState-shaped dict at the langgraph boundary (Pregel generics don't
    # track the input schema through compile()).
    result = await graph.ainvoke(
        cast("Any", {"messages": [HumanMessage(content="The gooseneck kettle should be $39.99 now")]}),
        config=config,
        context=Context(),
    )
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
