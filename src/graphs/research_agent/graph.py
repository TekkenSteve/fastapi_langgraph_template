"""Entry point — the only file langgraph.json references.

One line of MCP composition: the wrapper resolves this graph's servers from
the deployment registry (langgraph.json ``mcp_servers``) and hands the tools
to the builder. Ops override: ``MCP_SERVER__ACME_KB``.

``per_run=True`` builds the agent per run. The agent owns a sandbox backend
(``build_backend``), and a load-time-compiled graph would share that one
instance — and its file table — across every run and user. Tool loads are
served from the MCP fingerprint cache, so the per-run build does not
re-handshake the servers.
"""

from agent_server.contracts import with_mcp_tools
from research_agent.agent import build_research_agent

graph = with_mcp_tools(build_research_agent, servers=["acme-kb"], per_run=True)
