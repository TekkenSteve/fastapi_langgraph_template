"""Tools local to the research agent.

Shared tools (web_search) come from shared/tools/; only graph-specific
helpers live here.
"""

from langchain_core.tools import tool


@tool
def think_tool(reflection: str) -> str:
    """Record a plan or reflect on evidence gathered so far.

    Use before delegating and after each sub-agent returns, to decide what
    remains unanswered. Does not fetch new information.
    """
    return f"Reflection recorded: {reflection}"
