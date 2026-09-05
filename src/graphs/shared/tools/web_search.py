"""Web search tool.

Simulated by default so the template runs without extra API keys. Swap the
body for Tavily/Exa/SerpAPI when you have a key — the contract stays.

Search results are third-party text: they pass through the shared fence
(sanitize + wrap) before the model reads them, so a hostile page cannot
smuggle instructions into the prompt.
"""

from langchain_core.tools import tool

from shared.fencing import SHARED_FENCE


@tool
async def web_search(query: str, max_results: int = 3) -> str:
    """Search the web for current information on a topic."""
    results = [
        {
            "title": f"Simulated result {i + 1} for '{query}'",
            "url": f"https://example.com/result-{i + 1}",
            "snippet": f"Simulated snippet about {query}.",
        }
        for i in range(max_results)
    ]
    return SHARED_FENCE.fence_payload(results)
