"""Structured tool results.

A gate rejection is not an error and an error is not a rejection — the model
acts differently on each. ToolMessage content carries a JSON envelope so the
model can tell the three apart and self-correct on ``blocked`` instead of
retrying blindly:

    {"status": "ok", "result": ...}
    {"status": "blocked", "gate": "provenance", "reason": "..."}
    {"status": "error", "reason": "..."}

Helpers return ``Command`` so tools update both their message and graph state
in one place.
"""

import json
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command


def tool_ok(result: Any, tool_call_id: str, *, state_update: dict[str, Any] | None = None) -> Command:
    content = json.dumps({"status": "ok", "result": result}, ensure_ascii=False, default=str)
    update: dict[str, Any] = {"messages": [ToolMessage(content, tool_call_id=tool_call_id)]}
    if state_update:
        update.update(state_update)
    return Command(update=update)


def tool_blocked(gate: str, reason: str, tool_call_id: str) -> Command:
    """A gate refused the call. ``gate`` names the rule so the model can route around it."""
    content = json.dumps({"status": "blocked", "gate": gate, "reason": reason}, ensure_ascii=False)
    return Command(update={"messages": [ToolMessage(content, tool_call_id=tool_call_id)]})


def tool_error(reason: str, tool_call_id: str) -> Command:
    """The call itself failed. A tool exception never ends the turn."""
    content = json.dumps({"status": "error", "reason": reason}, ensure_ascii=False)
    return Command(update={"messages": [ToolMessage(content, tool_call_id=tool_call_id)]})
