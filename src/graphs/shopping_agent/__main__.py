"""Standalone smoke run: uv run python -m shopping_agent

Requires a real LLM key (e.g. OPENAI_API_KEY) in .env or the environment.
"""

import asyncio
from typing import Any, cast

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from shop.backends import get_backend
from shopping_agent.builder import build_shopping_agent
from shopping_agent.state import Context


async def main() -> None:
    # MemorySaver stands in for the server's Postgres checkpointer here.
    graph = build_shopping_agent(get_backend()).compile(checkpointer=MemorySaver())
    config: RunnableConfig = {"configurable": {"thread_id": "smoke"}}

    # InputState-shaped dict at the langgraph boundary (Pregel generics don't
    # track the input schema through compile()).
    result = await graph.ainvoke(
        cast("Any", {"messages": [HumanMessage(content="I'm looking for a coffee maker")]}),
        config=config,
        context=Context(),
    )
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
