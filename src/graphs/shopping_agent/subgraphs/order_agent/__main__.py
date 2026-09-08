"""Standalone smoke run: uv run python -m shopping_agent.subgraphs.order_agent

Debug the order-tracking subgraph in isolation — no parent graph, no server.
Requires a real LLM key (e.g. OPENAI_API_KEY) in .env or the environment.
"""

import asyncio

from langchain_core.messages import HumanMessage

from shop.backends import get_backend
from shopping_agent.state import Context
from shopping_agent.subgraphs.order_agent.builder import build_order_agent


async def main() -> None:
    graph = build_order_agent(get_backend()).compile()
    config = {"configurable": {"user_id": "demo-user"}}

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="Where is my order?")]},
        config=config,
        context=Context(),
    )
    print(result["messages"][-1].content)
    # The subgraph's generative UI block (OrderStatusCard payload), if any:
    for block in result.get("presentations", []):
        print(f"[presentation] {block['component']}: {block['payload']}")


if __name__ == "__main__":
    asyncio.run(main())
