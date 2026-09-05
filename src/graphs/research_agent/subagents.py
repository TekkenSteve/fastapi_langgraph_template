"""Sub-agent declarations — pure data, no code.

Each entry becomes a ``task`` the parent agent can delegate to. Adding a
sub-agent is appending a dict here; no graph rewiring needed.
"""

from research_agent.prompts import DEEP_DIVE_PROMPT
from shared.tools.web_search import web_search

DEEP_DIVE_SUBAGENT = {
    "name": "deep-dive",
    "description": (
        "Investigate one focused sub-question in depth. Delegate a single, self-contained question at a time."
    ),
    "system_prompt": DEEP_DIVE_PROMPT,
    "tools": [web_search],
}

SUBAGENTS = [DEEP_DIVE_SUBAGENT]
