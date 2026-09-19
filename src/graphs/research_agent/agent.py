"""Declarative agent assembly — paradigm B (composed agent).

Set SANDBOX_PROVIDER (local|daytona|e2b) to give the agent a real shell:
the sandbox becomes the default backend and skill scripts become runnable.

No explicit topology: the agent loop is provided by deepagents; behavior is
shaped by composing capabilities (tools, subagents, middleware, backend).
Contrast with paradigm A (shopping_agent) where the topology is explicit.

Skills ship as data in ``skills/`` and are served read-only through a
CompositeBackend: ``/skills/`` routes to the package directory, everything
else goes to the ephemeral state backend (the agent's scratch space).
User-tier skills from the skill hub are materialized per run into that
scratch space under ``/user-skills/`` by SkillRouterMiddleware.
"""

import operator
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, NotRequired, cast

from deepagents import DeepAgentState, create_deep_agent
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.state import StateBackend
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from hub.queries import load_user_skill_contents
from research_agent.prompts import RESEARCH_SYSTEM_PROMPT
from research_agent.subagents import SUBAGENTS
from research_agent.tools import make_plan_tool, think_tool
from shared.middleware.audit_log import AuditLogMiddleware
from shared.middleware.skill_router import LLMSkillSelector, SkillRouterMiddleware
from shared.sandbox import make_sandbox_backend
from shared.tools.web_search import web_search

DEFAULT_MODEL = "openai:gpt-4o-mini"


class ResearchAgentState(DeepAgentState):
    """DeepAgentState plus the generative-UI presentations channel."""

    presentations: NotRequired[Annotated[list[dict], operator.add]]


SKILLS_DIR = Path(__file__).parent / "skills"


def build_backend() -> CompositeBackend:
    """The agent's backend: read-only builtin skills route + scratch default.

    Script execution bridge (shared/sandbox.py): with SANDBOX_PROVIDER set the
    scratch space AND materialized user skills live in the sandbox and the
    execute tool appears; unset keeps everything ephemeral and inert.
    """
    skills_backend = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)
    sandbox = make_sandbox_backend(os.environ.get("SANDBOX_PROVIDER", ""))
    return CompositeBackend(
        default=sandbox or StateBackend(),
        routes={"/skills/": skills_backend},
    )


def build_research_agent(mcp_tools: Sequence[BaseTool] = (), model: str = DEFAULT_MODEL) -> CompiledStateGraph:
    backend = build_backend()
    # SkillRouterMiddleware = SkillsMiddleware + per-request top-k selection.
    # top_k above the catalog size behaves exactly like the static listing;
    # shrink the catalog or raise the count later without touching the graph.
    # user_skills_loader opts into the skill hub: the caller's installed
    # skills are materialized under /user-skills/ (auto-appended to sources,
    # so they override same-named builtins).
    skills = SkillRouterMiddleware(
        backend=backend,
        sources=[("/skills/", "Project")],
        selector=LLMSkillSelector(model, top_k=5),
        top_k=5,
        user_skills_loader=load_user_skill_contents,
    )

    # deepagents' built-ins come free: todo planning, virtual filesystem
    # (ls/read_file/write_file/edit_file), and the `task` delegation tool.
    return create_deep_agent(
        model=model,
        tools=[web_search, think_tool, make_plan_tool(), *mcp_tools],
        system_prompt=RESEARCH_SYSTEM_PROMPT,
        subagents=SUBAGENTS,
        # Our SkillRouterMiddleware widens abefore_agent to dict (same
        # contravariance its parent suppresses) — cast at the deepagents
        # boundary.
        middleware=[AuditLogMiddleware(), cast("AgentMiddleware[Any, None, Any]", skills)],
        backend=backend,
        state_schema=ResearchAgentState,
    )
