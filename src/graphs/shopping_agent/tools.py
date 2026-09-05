"""Shop-side tools exposed to the agent. The backend is injected via ``make_shop_tools`` —
tools never construct or import a concrete backend themselves.

Tools return ``Command`` so they can write both their ToolMessage and graph
state (e.g. search results feed the provenance gate via ``seen_product_ids``).
"""

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.runtime import get_runtime
from langgraph.types import Command, interrupt

from shop.backends import Cart, ShopBackend
from shopping_agent.gates import check_provenance, check_quantity
from shopping_agent.state import Context, State


def _user_id(config: RunnableConfig) -> str:
    """Server injects the authenticated identity into config.configurable."""
    return config.get("configurable", {}).get("user_id", "demo-user")


def _tool_message(text: str, tool_call_id: str) -> Command:
    return Command(update={"messages": [ToolMessage(text, tool_call_id=tool_call_id)]})


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
        text = "\n".join(f"{p.id}: {p.name} (${p.price:.2f}) — {p.description}" for p in products)
        return Command(
            update={
                "messages": [ToolMessage(text or "No products found.", tool_call_id=tool_call_id)],
                "seen_product_ids": [p.id for p in products],
            }
        )

    @tool
    async def get_cart(
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Show the current cart contents."""
        cart = await backend.get_cart(_user_id(config))
        return _tool_message(_format_cart(cart), tool_call_id)

    @tool
    async def add_to_cart(
        product_id: str,
        quantity: int,
        state: Annotated[State, InjectedState],
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Add a product to the cart. The id must come from a search result."""
        cap = get_runtime(Context).context.max_quantity_per_line
        for error in (check_provenance(state.seen_product_ids, product_id), check_quantity(quantity, cap)):
            if error is not None:
                return _tool_message(f"Cannot add to cart: {error}", tool_call_id)
        cart = await backend.add_to_cart(_user_id(config), product_id, quantity)
        return _tool_message(f"Added {quantity} × {product_id}.\n{_format_cart(cart)}", tool_call_id)

    @tool
    async def checkout(
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Check out the current cart. Always asks the customer to confirm first."""
        user_id = _user_id(config)
        cart = await backend.get_cart(user_id)
        if not cart.lines:
            return _tool_message("Your cart is empty — nothing to check out.", tool_call_id)

        approved = interrupt({"type": "checkout_approval", "cart": _format_cart(cart)})
        if not approved:
            return _tool_message("Checkout cancelled; the cart is unchanged.", tool_call_id)

        order = await backend.checkout(user_id)
        return _tool_message(f"Order placed: {order.order_id}", tool_call_id)

    @tool
    async def remember_preference(
        key: str,
        value: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Remember a customer preference (e.g. dietary, budget) for future sessions."""
        store = get_runtime(Context).store
        if store is None:
            return _tool_message("Long-term memory is unavailable in this run.", tool_call_id)
        user_id = _user_id(config)
        namespace = ("preferences",)
        existing = await store.aget(namespace, user_id)
        preferences = dict(existing.value) if existing else {}
        preferences[key] = value
        await store.aput(namespace, user_id, preferences)
        return _tool_message(f"Noted: {key} = {value}. I'll remember that.", tool_call_id)

    return [search_products, get_cart, add_to_cart, checkout, remember_preference]
