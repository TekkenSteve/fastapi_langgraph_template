"""State and context for the shopping agent.

State  = data that evolves during a run (messages, routing intent, …).
Context = per-run configuration that never changes mid-run (model, limits).
"""

import operator
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from langgraph.managed import IsLastStep

Intent = Literal["shop", "policy", "order", "chat"]


@dataclass
class InputState:
    """External interface: what callers provide."""

    messages: Annotated[Sequence[AnyMessage], add_messages] = field(default_factory=list)


@dataclass
class State(InputState):
    """Full internal state shared by all nodes."""

    intent: Intent = "chat"
    # Provenance gate: only product ids returned by search this session may be
    # written to the cart. Appended by the search_products tool.
    seen_product_ids: Annotated[list[str], operator.add] = field(default_factory=list)
    # Long-term preferences loaded from the server store by the initialize node.
    preferences: dict[str, Any] = field(default_factory=dict)
    # Managed by LangGraph (recursion_limit): True on the last allowed step.
    is_last_step: IsLastStep = field(default=False)


@dataclass(kw_only=True)
class Context:
    """Per-run configuration (override via the run's context parameter)."""

    model: str = "openai/gpt-4o-mini"
    max_quantity_per_line: int = 5
