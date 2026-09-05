"""Shop backend port and demo implementation.

Neither the agent nor the REST API touches real systems directly — every
surface talks to the ``ShopBackend`` protocol. To go live, implement
the protocol over your catalog/cart/order systems and switch ``get_backend()``
to return it.
"""

from dataclasses import dataclass, field
from typing import Protocol


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


class ShopBackend(Protocol):
    """Tool contracts the shop host must implement."""

    async def search_products(self, query: str, *, limit: int = 5) -> list[Product]: ...
    async def get_cart(self, user_id: str) -> Cart: ...
    async def add_to_cart(self, user_id: str, product_id: str, quantity: int) -> Cart: ...
    async def checkout(self, user_id: str) -> Order: ...
    async def get_orders(self, user_id: str, *, limit: int = 5) -> list[Order]: ...
    async def search_policies(self, query: str, *, limit: int = 3) -> list[str]: ...


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

    async def checkout(self, user_id: str) -> Order:
        cart = await self.get_cart(user_id)
        self._order_seq += 1
        order = Order(order_id=f"order-{self._order_seq:04d}", lines=list(cart.lines))
        self._orders.setdefault(user_id, []).append(order)
        cart.lines.clear()
        return order

    async def get_orders(self, user_id: str, *, limit: int = 5) -> list[Order]:
        return self._orders.get(user_id, [])[-limit:]

    async def search_policies(self, query: str, *, limit: int = 3) -> list[str]:
        return _DEMO_POLICIES[:limit]


def get_backend() -> ShopBackend:
    """Select the backend implementation.

    Demo default is a process-wide in-memory fake, shared by the agent and the
    REST routes (http.py) so both surfaces see the same carts and orders.
    Point this at your real implementation (or select via settings) when
    integrating live systems.
    """
    global _default_backend
    if _default_backend is None:
        _default_backend = FakeShopBackend()
    return _default_backend


_default_backend: ShopBackend | None = None
