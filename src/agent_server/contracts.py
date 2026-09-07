"""Public contracts for graph authors.

This is the ONLY agent_server module graphs may import — the framework's
stable API surface toward user graphs (same role langgraph_sdk.ServerRuntime
plays for LangGraph Platform).
"""

from agent_server.repo.graphs.mcp_loader import with_mcp_tools

__all__ = ["with_mcp_tools"]
