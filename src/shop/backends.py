"""Shop backend port and demo implementation.

Neither the agent nor the REST API touches real systems directly — every
surface talks to the ``ShopBackend`` protocol. To go live, implement
the protocol over your catalog/cart/order systems and switch ``get_backend()``
to return it.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    price: float
    description: str


@dataclass
class CartLine:
    product_id: str
    quantity: int


@dataclass
class Cart:
    lines: list[CartLine] = field(default_factory=list)


@dataclass(frozen=True)
class Order:
    order_id: str
    lines: list[CartLine]
    status: str = "processing"


@dataclass
class StagedChange:
    change_id: str
    kind: str  # e.g. "price_change"
    target_id: str  # e.g. a product id
    payload: dict[str, Any]
    status: str = "pending"  # pending -> applied | discarded


class ShopBackend(Protocol):
    """Tool contracts the shop host must implement — customer surface."""

    async def search_products(self, query: str, *, limit: int = 5) -> list[Product]: ...
    async def get_cart(self, user_id: str) -> Cart: ...
    async def add_to_cart(self, user_id: str, product_id: str, quantity: int) -> Cart: ...
    async def checkout_handoff(self, user_id: str) -> str:
        """Return a hosted checkout URL for the cart. Completing the order is the
        host's job — the protocol deliberately has no order-placement method."""
        ...

    async def get_orders(self, user_id: str, *, limit: int = 5) -> list[Order]: ...
    async def search_policies(self, query: str, *, limit: int = 3) -> list[str]: ...


class MerchantBackend(Protocol):
    """Tool contracts for the staff-facing (merchant) surface.

    Every write is a *staged change*: stage → review → apply/discard. Nothing
    mutates a listing directly; `apply_change` is the only way a staged change
    takes effect, and callers are expected to gate it behind host approval.
    """

    async def get_listing(self, product_id: str) -> Product | None: ...
    async def stage_price_change(self, user_id: str, product_id: str, new_price: float) -> StagedChange: ...
    async def get_pending_changes(self, user_id: str) -> list[StagedChange]: ...
    async def apply_change(self, user_id: str, change_id: str) -> StagedChange: ...
    async def discard_change(self, user_id: str, change_id: str) -> bool: ...
    async def analytics_query(self, query: str, *, max_rows: int = 50) -> list[dict[str, Any]]:
        """Read-only analytics. Implementations must accept a single SELECT
        only and honour row/character budgets; the caller enforces them too."""
        ...


_DEMO_CATALOG = [
    Product("coffee-maker-01", "Acme Drip Coffee Maker", 79.99, "12-cup drip coffee maker, programmable timer."),
    Product("coffee-maker-02", "Acme Espresso Machine", 249.00, "15-bar pump espresso machine with steam wand."),
    Product("kettle-01", "Acme Gooseneck Kettle", 45.50, "1L gooseneck kettle, ±1°C temperature control."),
    Product("grinder-01", "Acme Burr Grinder", 99.00, "Conical burr grinder, 40 grind settings."),
    Product("beans-01", "Acme Single-Origin Beans 1kg", 22.00, "Washed Ethiopian Yirgacheffe, medium roast."),
    Product("scale-01", "Acme Coffee Scale", 35.00, "0.1g precision scale with brew timer."),
]

_DEMO_POLICIES = [
    "Returns: unopened items may be returned within 30 days for a full refund.",
    "Shipping: free shipping on orders over $50; otherwise $5.99 flat rate.",
    "Warranty: all appliances carry a 2-year limited warranty.",
]


class FakeShopBackend:
    """In-memory demo backend. Data resets on restart — by design."""

    def __init__(self) -> None:
        self._carts: dict[str, Cart] = {}
        self._orders: dict[str, list[Order]] = {}
        self._order_seq = 0
        self._changes: dict[str, list[StagedChange]] = {}
        self._change_seq = 0

    async def search_products(self, query: str, *, limit: int = 5) -> list[Product]:
        terms = query.lower().split()
        if not terms:
            return list(_DEMO_CATALOG)[:limit]
        matches = [p for p in _DEMO_CATALOG if any(t in f"{p.name} {p.description}".lower() for t in terms)]
        return matches[:limit]

    async def get_cart(self, user_id: str) -> Cart:
        return self._carts.setdefault(user_id, Cart())

    async def add_to_cart(self, user_id: str, product_id: str, quantity: int) -> Cart:
        cart = await self.get_cart(user_id)
        for line in cart.lines:
            if line.product_id == product_id:
                line.quantity += quantity
                return cart
        cart.lines.append(CartLine(product_id=product_id, quantity=quantity))
        return cart

    async def checkout_handoff(self, user_id: str) -> str:
        return f"https://checkout.example.com/{user_id}"

    async def checkout(self, user_id: str) -> Order:
        """Test/demo helper: complete the cart into an order.

        Deliberately NOT on ShopBackend — production agents hand checkout off
        to the host. Tests use this to seed orders.
        """
        cart = await self.get_cart(user_id)
        self._order_seq += 1
        order = Order(order_id=f"order-{self._order_seq:04d}", lines=list(cart.lines))
        self._orders.setdefault(user_id, []).append(order)
        cart.lines.clear()
        return order

    async def get_orders(self, user_id: str, *, limit: int = 5) -> list[Order]:
        return self._orders.get(user_id, [])[-limit:]

    # ---- merchant surface: staged changes ----

    async def get_listing(self, product_id: str) -> Product | None:
        return next((p for p in _DEMO_CATALOG if p.id == product_id), None)

    async def stage_price_change(self, user_id: str, product_id: str, new_price: float) -> StagedChange:
        self._change_seq += 1
        product = await self.get_listing(product_id)
        if product is None:
            raise KeyError(f"unknown product: {product_id}")
        change = StagedChange(
            change_id=f"change-{self._change_seq:04d}",
            kind="price_change",
            target_id=product_id,
            payload={"old_price": product.price, "new_price": new_price},
        )
        self._changes.setdefault(user_id, []).append(change)
        return change

    async def get_pending_changes(self, user_id: str) -> list[StagedChange]:
        return [c for c in self._changes.get(user_id, []) if c.status == "pending"]

    async def apply_change(self, user_id: str, change_id: str) -> StagedChange:
        change = self._find_change(user_id, change_id)
        if change.kind == "price_change":
            product = await self.get_listing(change.target_id)
            if product is not None:
                _DEMO_CATALOG[_DEMO_CATALOG.index(product)] = Product(
                    product.id, product.name, change.payload["new_price"], product.description
                )
        change.status = "applied"
        return change

    async def discard_change(self, user_id: str, change_id: str) -> bool:
        self._find_change(user_id, change_id).status = "discarded"
        return True

    def _find_change(self, user_id: str, change_id: str) -> StagedChange:
        for change in self._changes.get(user_id, []):
            if change.change_id == change_id and change.status == "pending":
                return change
        raise KeyError(f"no pending change: {change_id}")

    async def search_policies(self, query: str, *, limit: int = 3) -> list[str]:
        return _DEMO_POLICIES[:limit]

    async def analytics_query(self, query: str, *, max_rows: int = 50) -> list[dict[str, Any]]:
        """Demo analytics: sales lines derived from recorded orders."""
        rows = [
            {"product_id": line.product_id, "units": line.quantity}
            for orders in self._orders.values()
            for order in orders
            for line in order.lines
        ]
        return rows[:max_rows]


_default_backend: FakeShopBackend | None = None


def get_backend() -> ShopBackend:
    """Select the backend implementation.

    Demo default is a process-wide in-memory fake, shared by every surface
    (agent graphs and the REST routes in api.py) so they all see the same
    carts, orders and staged changes. Point this at your real implementation
    (or select via settings) when integrating live systems.
    """
    global _default_backend
    if _default_backend is None:
        _default_backend = FakeShopBackend()
    return _default_backend


def get_merchant_backend() -> MerchantBackend:
    """The merchant-surface view of the same shared backend instance."""
    return get_backend()  # FakeShopBackend implements both protocols
