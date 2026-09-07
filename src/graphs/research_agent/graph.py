"""Entry point — the only file langgraph.json references.

One line of MCP composition: the wrapper resolves this graph's servers from
the deployment registry (langgraph.json ``mcp_servers``) and hands the tools
to the builder. Ops override: ``MCP_SERVER__ACME_KB``.
"""

from agent_server.contracts import with_mcp_tools
from research_agent.agent import build_research_agent

graph = with_mcp_tools(build_research_agent, servers=["acme-kb"])
