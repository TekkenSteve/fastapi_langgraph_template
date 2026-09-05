"""Merchant tools — every write is staged, every apply interrupts for approval."""

import asyncio
import json
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.runtime import get_runtime
from langgraph.types import Command, interrupt

from merchant_agent.gates import (
    check_change_provenance,
    check_listing_provenance,
    check_price_move,
)
from merchant_agent.state import Context, State
from shared.tooling import tool_blocked, tool_ok
from shop.backends import MerchantBackend


def _user_id(config: RunnableConfig) -> str:
    return config.get("configurable", {}).get("user_id", "demo-staff")


def _msg(text: str, tool_call_id: str) -> Command:
    return Command(update={"messages": [ToolMessage(text, tool_call_id=tool_call_id)]})


def _format_change(change) -> str:
    if change.kind == "price_change":
        p = change.payload
        return f"{change.change_id}: {change.target_id} price {p['old_price']} → {p['new_price']} ({change.status})"
    return f"{change.change_id}: {change.kind} on {change.target_id} ({change.status})"


def make_merchant_tools(backend: MerchantBackend) -> list:
    """Build the merchant tool list with the backend bound via closure."""

    @tool
    async def get_listing(product_id: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Read a listing before staging any change to it."""
        product = await backend.get_listing(product_id)
        if product is None:
            return _msg(f"No such product: {product_id}", tool_call_id)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"{product.id}: {product.name} — current price ${product.price:.2f}",
                        tool_call_id=tool_call_id,
                    )
                ],
                "seen_listing_ids": [product.id],
            }
        )

    @tool
    async def stage_price_change(
        product_id: str,
        new_price: float,
        state: Annotated[State, InjectedState],
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Stage a price change for review. Nothing is applied by this call."""
        if error := check_listing_provenance(state.seen_listing_ids, product_id):
            return _msg(f"Cannot stage: {error}", tool_call_id)
        product = await backend.get_listing(product_id)
        if product is None:
            return _msg(f"No such product: {product_id}", tool_call_id)
        cap = get_runtime(Context).context.max_price_move_pct
        if error := check_price_move(product.price, new_price, cap):
            return _msg(f"Cannot stage: {error}", tool_call_id)

        change = await backend.stage_price_change(_user_id(config), product_id, new_price)
        return Command(
            update={
                "messages": [ToolMessage(f"Staged for review: {_format_change(change)}", tool_call_id=tool_call_id)],
                "staged_change_ids": [change.change_id],
            }
        )

    @tool
    async def get_pending_changes(config: RunnableConfig, tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """List staged changes awaiting review."""
        changes = await backend.get_pending_changes(_user_id(config))
        text = "\n".join(_format_change(c) for c in changes) or "No pending changes."
        return _msg(text, tool_call_id)

    @tool
    async def apply_change(
        change_id: str,
        state: Annotated[State, InjectedState],
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Apply a staged change. Guardrails are re-checked with the config in
        force *now* (they may have changed since staging), then the call always
        pauses for staff approval."""
        if error := check_change_provenance(state.staged_change_ids, change_id):
            return _msg(f"Cannot apply: {error}", tool_call_id)

        # Re-validate guardrails at apply time against the live config.
        pending = await backend.get_pending_changes(_user_id(config))
        change = next((c for c in pending if c.change_id == change_id), None)
        if change is None:
            return _msg(f"Cannot apply: change {change_id} is no longer pending.", tool_call_id)
        if change.kind == "price_change":
            cap = get_runtime(Context).context.max_price_move_pct
            if error := check_price_move(change.payload["old_price"], change.payload["new_price"], cap):
                return tool_blocked("guardrail", error, tool_call_id)

        approved = interrupt({"type": "apply_change_approval", "change_id": _format_change(change)})
        if not approved:
            return _msg(f"Change {change_id} was not approved; it stays pending.", tool_call_id)

        applied = await backend.apply_change(_user_id(config), change_id)
        return _msg(f"Applied: {_format_change(applied)}", tool_call_id)

    @tool
    async def discard_change(
        change_id: str,
        state: Annotated[State, InjectedState],
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Discard a staged change without applying it."""
        if error := check_change_provenance(state.staged_change_ids, change_id):
            return _msg(f"Cannot discard: {error}", tool_call_id)
        await backend.discard_change(_user_id(config), change_id)
        return _msg(f"Discarded: {change_id}", tool_call_id)

    @tool
    async def analytics_query(
        query: str,
        state: Annotated[State, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Run a read-only analytics query. Budgeted: single SELECT only,
        capped rows/characters, wall-clock timeout, limited calls per turn."""
        ctx = get_runtime(Context).context
        if not ctx.enable_analytics:
            return tool_blocked("config", "analytics is disabled for this deployment.", tool_call_id)
        if state.analytics_calls >= ctx.analytics_max_calls_per_turn:
            return tool_blocked(
                "budget",
                f"analytics call budget exhausted ({ctx.analytics_max_calls_per_turn} per turn).",
                tool_call_id,
            )
        normalized = " ".join(query.split())
        if not normalized.lower().startswith("select") or ";" in normalized:
            return tool_blocked("read_only", "analytics accepts a single SELECT statement only.", tool_call_id)

        try:
            rows = await asyncio.wait_for(
                backend.analytics_query(query, max_rows=ctx.analytics_max_rows),
                timeout=ctx.analytics_timeout_secs,
            )
        except TimeoutError:
            return tool_blocked(
                "budget", f"query exceeded the {ctx.analytics_timeout_secs}s wall-clock budget.", tool_call_id
            )

        text = json.dumps(rows, ensure_ascii=False, default=str)
        if len(text) > ctx.analytics_max_chars:
            text = text[: ctx.analytics_max_chars] + " ...[truncated]"
        return tool_ok(text, tool_call_id, state_update={"analytics_calls": state.analytics_calls + 1})

    return [get_listing, stage_price_change, get_pending_changes, apply_change, discard_change, analytics_query]
