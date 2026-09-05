"""Entry point — the only file langgraph.json references.

Exported as a 0-arg factory so the module is import-safe (the model client is
only constructed when the server loads graphs at startup, with .env loaded).
The three entry forms are: static ``graph`` object / 0-arg factory /
per-request factory (see graphs/factory/).
"""

from research_agent.agent import build_research_agent


def graph():
    return build_research_agent()
