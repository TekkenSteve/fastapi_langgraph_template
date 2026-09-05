"""Shopping agent — canonical graph-package example.

Demonstrates the full graph authoring conventions (see src/graphs/README.md):
thin graph.py entry, builder wiring, per-node files, pure edges, an injected
backend protocol with a demo implementation, business gates, HITL checkout,
and long-term memory via the server store.
"""

from shopping_agent.graph import graph

__all__ = ["graph"]
