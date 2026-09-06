"""Order-tracking sub-agent state.

Overlapping channel with the parent graph: ``messages`` (shared reducer, so
the conversation flows in and the answer flows back). ``orders`` is local to
this subgraph and discarded on return.
"""

import operator
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


@dataclass
class OrderState:
    messages: Annotated[Sequence[AnyMessage], add_messages] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    # Shared channel with the parent graph: rendered blocks flow up.
    presentations: Annotated[list[dict[str, Any]], operator.add] = field(default_factory=list)
