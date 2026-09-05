"""Declarative agent assembly — paradigm B (composed agent).

No explicit topology: the agent loop is provided by deepagents; behavior is
shaped by composing capabilities (tools, subagents, middleware, backend).
Contrast with paradigm A (shopping_agent) where the topology is explicit.

Skills ship as data in ``skills/`` and are served read-only through a
CompositeBackend: ``/skills/`` routes to the package directory, everything
else goes to the ephemeral state backend (the agent's scratch space).
"""

from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.state import StateBackend
from langgraph.graph.state import CompiledStateGraph

from research_agent.prompts import RESEARCH_SYSTEM_PROMPT
from research_agent.subagents import SUBAGENTS
from research_agent.tools import think_tool
from shared.middleware.audit_log import AuditLogMiddleware
from shared.middleware.skill_router import LLMSkillSelector, SkillRouterMiddleware
from shared.tools.web_search import web_search

DEFAULT_MODEL = "openai:gpt-4o-mini"

SKILLS_DIR = Path(__file__).parent / "skills"


def build_research_agent(model: str = DEFAULT_MODEL) -> CompiledStateGraph:
    skills_backend = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)
    backend = CompositeBackend(
        default=StateBackend(),  # scratch space stays ephemeral per run
        routes={"/skills/": skills_backend},
    )
    # SkillRouterMiddleware = SkillsMiddleware + per-request top-k selection.
    # top_k above the catalog size behaves exactly like the static listing;
    # shrink the catalog or raise the count later without touching the graph.
    skills = SkillRouterMiddleware(
        backend=backend,
        sources=[("/skills/", "Project")],
        selector=LLMSkillSelector(model, top_k=5),
        top_k=5,
    )

    # deepagents' built-ins come free: todo planning, virtual filesystem
    # (ls/read_file/write_file/edit_file), and the `task` delegation tool.
    return create_deep_agent(
        model=model,
        tools=[web_search, think_tool],
        system_prompt=RESEARCH_SYSTEM_PROMPT,
        subagents=SUBAGENTS,
        middleware=[AuditLogMiddleware(), skills],
        backend=backend,
    )
