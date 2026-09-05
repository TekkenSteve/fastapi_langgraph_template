"""Declarative agent assembly — paradigm B (composed agent).

No explicit topology: the agent loop is provided by deepagents; behavior is
shaped by composing capabilities (tools, subagents, middleware, backend).
Contrast with paradigm A (shopping_agent) where the topology is explicit.
"""

from deepagents import create_deep_agent
from langgraph.graph.state import CompiledStateGraph

from research_agent.prompts import RESEARCH_SYSTEM_PROMPT
from research_agent.subagents import SUBAGENTS
from research_agent.tools import think_tool
from shared.middleware.audit_log import AuditLogMiddleware
from shared.tools.web_search import web_search

DEFAULT_MODEL = "openai:gpt-4o-mini"


def build_research_agent(model: str = DEFAULT_MODEL) -> CompiledStateGraph:
    # deepagents' built-ins come free: todo planning, virtual filesystem
    # (ls/read_file/write_file/edit_file), and the `task` delegation tool.
    return create_deep_agent(
        model=model,
        tools=[web_search, think_tool],
        system_prompt=RESEARCH_SYSTEM_PROMPT,
        subagents=SUBAGENTS,
        middleware=[AuditLogMiddleware()],
    )
