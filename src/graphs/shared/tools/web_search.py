"""Web search tool.

Simulated by default so the template runs without extra API keys. Swap the
body for Tavily/Exa/SerpAPI when you have a key — the contract stays.
"""

from langchain_core.tools import tool


@tool
async def web_search(query: str, max_results: int = 3) -> dict:
    """Search the web for current information on a topic."""
    return {
        "query": query,
        "results": [
            {
                "title": f"Simulated result {i + 1} for '{query}'",
                "url": f"https://example.com/result-{i + 1}",
                "snippet": f"Simulated snippet about {query}.",
            }
            for i in range(max_results)
        ],
    }
