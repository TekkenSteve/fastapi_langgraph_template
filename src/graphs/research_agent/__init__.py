"""Research agent — paradigm B (composed agent) example.

Behavior is shaped by declaring capabilities (tools, subagents, middleware)
rather than wiring an explicit graph. See src/graphs/README.md.
"""

from research_agent.agent import build_research_agent

__all__ = ["build_research_agent"]
