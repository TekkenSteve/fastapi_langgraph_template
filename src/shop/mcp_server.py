"""The shop domain's MCP surface (stdio transport) — alongside api.py (REST).

Same domain, two surfaces: agents reach the internal knowledge base through
MCP, browsers/clients through REST. Register it in langgraph.json
``mcp_servers``; graphs compose it via ``with_mcp_tools(build, servers=["acme-kb"])``.

Run standalone for inspection:  .venv/bin/python src/shop/mcp_server.py
SSE mode (for the dev inspector): MCP_TRANSPORT=sse MCP_PORT=8081 ...
"""

import os

from mcp.server.fastmcp import FastMCP

# A pretend internal knowledge base — the kind of source a research agent
# reaches for that public web search cannot see.
_KB = {
    "acme": "Acme Corp internal wiki: founded 2019, coffee equipment division launched 2023, 340 employees.",
    "pricing": "Internal pricing policy: MAP enforcement on all coffee gear; discounts over 15% need VP sign-off.",
    "returns": "Internal ops note: espresso machines have a 4.2% DOA rate; grinders 1.1%.",
}

_mcp_transport = os.environ.get("MCP_TRANSPORT", "stdio")  # stdio (agent default) | sse (inspector/dev)

mcp = FastMCP(
    "acme-kb",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_PORT", "8081")),
)


@mcp.tool()
def kb_search(query: str) -> str:
    """Search the internal knowledge base by keyword."""
    query_lower = query.lower()
    hits = [v for k, v in _KB.items() if k in query_lower]
    return "\n".join(hits) if hits else "No internal entries matched."


@mcp.tool()
def kb_list_topics() -> str:
    """List available internal knowledge base topics."""
    return ", ".join(sorted(_KB))


if __name__ == "__main__":
    mcp.run(transport=_mcp_transport)
