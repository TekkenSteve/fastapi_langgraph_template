"""Tools local to the research agent.

Shared tools (web_search) come from shared/tools/; only graph-specific
helpers live here.
"""

import structlog
from langchain_core.tools import tool
from pydantic import Field

from shared.presentation import PresentationComponent, PresentationPayload, make_presentation_tool

logger = structlog.get_logger(__name__)


class PresentPlanPayload(PresentationPayload):
    """A research plan rendered as a checklist card."""

    title: str = Field(max_length=80)
    steps: list[str] = Field(min_length=1, max_length=8)


PRESENT_PLAN = PresentationComponent(
    name="present_plan",
    component="PlanChecklist",
    payload_model=PresentPlanPayload,
)


def make_plan_tool():
    return make_presentation_tool(
        PRESENT_PLAN,
        backend=None,  # no facts to join — a plan is model-authored content
        description="Render the research plan as a checklist card before delegating to sub-agents.",
    )


@tool
def think_tool(reflection: str) -> str:
    """Record a plan or reflect on evidence gathered so far.

    Use before delegating and after each sub-agent returns, to decide what
    remains unanswered. Does not fetch new information.
    """
    return f"Reflection recorded: {reflection}"
