"""Storefront tools. The backend is injected via ``make_shop_tools`` —
tools never construct or import a concrete backend themselves.

Conventions demonstrated here:
- Structured results (shared/tooling): ok / blocked (gate name) / error.
- Fenced payloads (shared/fencing): catalog text is third-party data, wrapped
  before the model reads it.
- Memory writes go through the shared write filter and lifecycle store.
- checkout is a handoff: the agent never completes an order itself.
"""

from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.runtime import get_runtime
from langgraph.types import Command, interrupt

from shared.fencing import SHARED_FENCE
from shared.memory import MemoryStore, MemoryWriteRejected
from shared.tooling import tool_blocked, tool_error, tool_ok
from shop.backends import Cart, ShopBackend
from shopping_agent.gates import check_cart_size, check_provenance, check_quantity
from shopping_agent.state import Context, State


def _user_id(config: RunnableConfig) -> str:
    """Server injects the authenticated identity into config.configurable."""
    return config.get("configurable", {}).get("user_id", "demo-user")


def _format_cart(cart: Cart) -> str:
    if not cart.lines:
        return "Your cart is empty."
    lines = "\n".join(f"- {line.product_id} × {line.quantity}" for line in cart.lines)
    return f"Cart:\n{lines}"


def make_shop_tools(backend: ShopBackend) -> list:
    """Build the tool list with the backend bound via closure."""

    @tool
    async def search_products(query: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Search the product catalog by free-text query."""
        products = await backend.search_products(query)
        payload = [{"id": p.id, "name": p.name, "price": p.price, "description": p.description} for p in products]
        return tool_ok(
            SHARED_FENCE.fence_payload(payload, max_chars=2000) if payload else "No products found.",
            tool_call_id,
            state_update={"seen_product_ids": [p.id for p in products]},
        )

    @tool
    async def get_cart(
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Show the current cart contents."""
        cart = await backend.get_cart(_user_id(config))
        return tool_ok(_format_cart(cart), tool_call_id)

    @tool
    async def add_to_cart(
        product_id: str,
        quantity: int,
        state: Annotated[State, InjectedState],
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Add a product to the cart. The id must come from a search result."""
        ctx = get_runtime(Context).context
        cart = await backend.get_cart(_user_id(config))
        for gate, error in (
            ("provenance", check_provenance(state.seen_product_ids, product_id)),
            ("quantity_limit", check_quantity(quantity, ctx.max_quantity_per_line)),
            ("cart_size", check_cart_size(len(cart.lines), ctx.max_cart_lines)),
        ):
            if error is not None:
                return tool_blocked(gate, error, tool_call_id)
        cart = await backend.add_to_cart(_user_id(config), product_id, quantity)
        return tool_ok(f"Added {quantity} × {product_id}.\n{_format_cart(cart)}", tool_call_id)

    @tool
    async def checkout(
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Check out the current cart. Pauses for customer confirmation, then
        hands off to the host's checkout — the agent never completes an order."""
        user_id = _user_id(config)
        cart = await backend.get_cart(user_id)
        if not cart.lines:
            return tool_blocked("empty_cart", "Your cart is empty — nothing to check out.", tool_call_id)

        approved = interrupt({"type": "checkout_approval", "cart": _format_cart(cart)})
        if not approved:
            return tool_ok("Checkout cancelled; the cart is unchanged.", tool_call_id)

        handoff_url = await backend.checkout_handoff(user_id)
        return tool_ok(f"Ready to check out — complete it here: {handoff_url}", tool_call_id)

    @tool
    async def remember_preference(
        key: str,
        value: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Remember a customer preference (e.g. dietary, budget) for future sessions."""
        ctx = get_runtime(Context)
        if not ctx.context.enable_memory:
            return tool_blocked("config", "memory is disabled for this deployment.", tool_call_id)
        store = ctx.store
        if store is None:
            return tool_error("Long-term memory is unavailable in this run.", tool_call_id)
        memory = MemoryStore(store)
        try:
            await memory.save(_user_id(config), key, value)
        except MemoryWriteRejected as exc:
            return tool_blocked("memory_write_filter", str(exc), tool_call_id)
        return tool_ok(f"Noted: {key} = {value}. I'll remember that.", tool_call_id)

    return [search_products, get_cart, add_to_cart, checkout, remember_preference]
