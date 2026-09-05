"""Shop product package: domain port + surfaces.

Graphs (graphs/shopping_agent) and REST routes (api.py) are both *surfaces*
over the same backend port. To go live, implement ShopBackend over your
catalog/cart/order systems and switch get_backend() to return it.
"""

from shop.backends import (
    Cart,
    CartLine,
    FakeShopBackend,
    MerchantBackend,
    Order,
    Product,
    ShopBackend,
    StagedChange,
    get_backend,
    get_merchant_backend,
)

__all__ = [
    "Cart",
    "CartLine",
    "FakeShopBackend",
    "Order",
    "Product",
    "MerchantBackend",
    "ShopBackend",
    "StagedChange",
    "get_backend",
    "get_merchant_backend",
]
