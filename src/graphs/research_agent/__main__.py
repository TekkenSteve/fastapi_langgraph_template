"""Standalone smoke run: uv run python -m research_agent

Requires a real LLM key (e.g. OPENAI_API_KEY) in .env or the environment.
"""

import asyncio

from langchain_core.messages import HumanMessage

from research_agent.agent import build_research_agent


async def main() -> None:
    graph = build_research_agent()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="Give me a one-paragraph overview of pour-over coffee")]}
    )
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
