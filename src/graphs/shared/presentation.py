"""Presentation: validated, server-enriched UI payloads for generative UI.

The model selects and annotates; every fact on a component is joined
server-side. A presentation tool validates the model's arguments against a
payload model, runs the enrich hook (which joins backend data onto the
component and drops anything without session provenance), writes the result
to graph state (the UI source of truth — survives SSE reconnection), and
emits a `ui` custom-stream event for live listeners.

Adapted from anthropics/commerce-agents `commerce_common/presentation.py`
(Apache-2.0), (c) 2026 Anthropic PBC, onto LangGraph tools + custom stream.
"""

from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.fencing import SHARED_FENCE
from shared.tooling import tool_blocked, tool_error, tool_ok

CHIP_MAX_CHARS = 80


class PresentationRefused(ValueError):
    """Raised by an enrich hook when the call cannot render. ``gate`` names the
    gate that held it (→ blocked result); without a gate the result is an error."""

    def __init__(self, message: str, gate: str | None = None) -> None:
        super().__init__(message)
        self.gate = gate


class PresentationPayload(BaseModel):
    """Base of presentation payloads. Undeclared keys are dropped, not rejected:
    the tool's input schema is what constrains the model."""

    model_config = ConfigDict(extra="ignore")


class SuggestionsPayload(PresentationPayload):
    """The turn's suggestion chips (1-4, short, plain text)."""

    suggestions: list[str] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def _sanitize_chips(self) -> "SuggestionsPayload":
        cleaned = [
            chip
            for chip in (" ".join(SHARED_FENCE.sanitize_text(c, CHIP_MAX_CHARS).split()) for c in self.suggestions)
            if chip
        ]
        if not cleaned:
            raise ValueError("every suggestion was empty after sanitization — send 1-4 short, plain-text suggestions.")
        self.suggestions = cleaned[:4]
        return self


@dataclass(frozen=True)
class EnrichmentContext:
    """What an enrich hook works with: the backend port, the caller's identity,
    and the provenance the session has earned. Append to ``notes`` anything the
    model should hear about (ids dropped, text removed)."""

    backend: Any
    user_id: str
    seen_ids: frozenset[str]
    notes: list[str] = field(default_factory=list)


class EnrichFn(Protocol):
    async def __call__(self, payload: PresentationPayload, context: EnrichmentContext) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PresentationComponent:
    """One presentation tool: the component the host renders, the payload model
    validating the model's arguments, and the hook joining server data onto it."""

    name: str
    component: str
    payload_model: type[PresentationPayload]
    enrich: EnrichFn | None = None


async def validate_and_enrich(
    spec: PresentationComponent,
    args: dict[str, Any],
    context: EnrichmentContext,
) -> dict[str, Any]:
    """Validate, enrich, return the component payload. Raises PresentationRefused."""
    payload = spec.payload_model.model_validate(args)
    if spec.enrich is None:
        return payload.model_dump(exclude_none=True)
    return await spec.enrich(payload, context)


def make_presentation_tool(
    spec: PresentationComponent,
    *,
    backend: Any,
    description: str,
) -> Any:
    """Build a presentation tool: validate → enrich → state write + ui event."""

    @tool(spec.name, description=description, args_schema=spec.payload_model)
    async def present(
        state: Annotated[Any, InjectedState],
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
        **args: Any,
    ) -> Command:
        seen = frozenset(getattr(state, "seen_product_ids", []) or [])
        user_id = config.get("configurable", {}).get("user_id", "demo-user")
        context = EnrichmentContext(backend=backend, user_id=user_id, seen_ids=seen)
        try:
            payload = await validate_and_enrich(spec, args, context)
        except PresentationRefused as refused:
            if refused.gate is not None:
                return tool_blocked(refused.gate, str(refused), tool_call_id)
            return tool_error(str(refused), tool_call_id)
        except ValueError as exc:
            return tool_error(f"Invalid {spec.name} payload: {exc}", tool_call_id)

        block = {"component": spec.component, "payload": payload}
        get_stream_writer()({"type": "ui", **block})  # live listeners
        note = f" Component shown to the customer. {(' '.join(context.notes))}".rstrip()
        return tool_ok(
            note,
            tool_call_id,
            state_update={"presentations": [block]},  # UI source of truth
        )

    return present
