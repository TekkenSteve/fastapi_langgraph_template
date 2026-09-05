"""Standalone smoke run: uv run python -m merchant_agent

Requires a real LLM key (e.g. OPENAI_API_KEY) in .env or the environment.
"""

import asyncio

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from merchant_agent.builder import build_merchant_agent
from merchant_agent.state import Context
from shop.backends import get_backend


async def main() -> None:
    graph = build_merchant_agent(get_backend()).compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "smoke", "user_id": "staff-1"}}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="The gooseneck kettle should be $39.99 now")]},
        config=config,
        context=Context(),
    )
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
