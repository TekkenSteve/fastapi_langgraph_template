"""Entry point — the only file langgraph.json references.

Stays this thin no matter how the package grows: internal refactors never
change the registration path.
"""

from shop.backends import get_backend
from shopping_agent.builder import build_shopping_agent

graph = build_shopping_agent(get_backend()).compile(name="Shopping Agent")
