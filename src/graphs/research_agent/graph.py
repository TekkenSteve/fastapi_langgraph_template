"""Entry point — the only file langgraph.json references.

One line of MCP composition: the wrapper resolves this graph's declared
servers and hands the tools to the builder. Resolution walks the three trust
tiers, highest first — the caller's own connections (``hub.queries``), the ops
env override (``MCP_SERVER__ACME_KB``), then the deployment registry
(langgraph.json ``mcp_servers``). A name the graph did not declare is never
resolved, so a user cannot add a server this graph did not opt into.

``user_scoped=True`` does two things. It resolves the caller's tier per run
(from the run's authenticated identity), and it makes this a per-request
factory — which is also what keeps the sandbox honest: the agent owns a
backend (``build_backend``), and a load-time-compiled graph would share that
one instance, and its file table, across every run and user. Deployment-tier
tool loads are served from the MCP fingerprint cache, so building per run does
not re-handshake the servers.
"""

from agent_server.contracts import with_mcp_tools
from hub.queries import load_connection_map
from research_agent.agent import build_research_agent

graph = with_mcp_tools(
    build_research_agent,
    servers=["acme-kb"],
    user_scoped=True,
    connection_provider=load_connection_map,
)
