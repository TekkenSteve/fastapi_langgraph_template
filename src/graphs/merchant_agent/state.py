"""State and context for the merchant agent (staff-facing)."""

import operator
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from langgraph.managed import IsLastStep


@dataclass
class InputState:
    messages: Annotated[Sequence[AnyMessage], add_messages] = field(default_factory=list)


@dataclass
class State(InputState):
    """Full internal state shared by all nodes."""

    # Staging provenance: only listings read via get_listing this session may
    # be staged; only changes staged this session may be applied/discarded.
    seen_listing_ids: Annotated[list[str], operator.add] = field(default_factory=list)
    staged_change_ids: Annotated[list[str], operator.add] = field(default_factory=list)
    is_last_step: IsLastStep = field(default=False)
    # Per-turn analytics budget (resets each run; incremented by analytics_query).
    analytics_calls: int = 0


@dataclass(kw_only=True)
class Context:
    """Per-run configuration (override via the run's context parameter)."""

    model: str = "openai/gpt-4o-mini"
    # Guardrail: a staged price move beyond this percentage is refused.
    max_price_move_pct: float = 20.0
    # Analytics budgets (read-only analysis queries).
    enable_analytics: bool = True
    analytics_max_rows: int = 50
    analytics_max_chars: int = 4000
    analytics_timeout_secs: float = 5.0
    analytics_max_calls_per_turn: int = 3
