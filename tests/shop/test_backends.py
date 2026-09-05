"""Unit tests for the FakeShopBackend demo implementation."""

from shop.backends import FakeShopBackend


async def test_search_matches_catalog_terms() -> None:
    backend = FakeShopBackend()
    results = await backend.search_products("coffee maker")
    assert any(p.id == "coffee-maker-01" for p in results)


async def test_search_returns_empty_for_unknown_terms() -> None:
    backend = FakeShopBackend()
    assert await backend.search_products("xylophone") == []


async def test_cart_lifecycle() -> None:
    backend = FakeShopBackend()
    cart = await backend.add_to_cart("u1", "kettle-01", 2)
    assert cart.lines[0].quantity == 2

    cart = await backend.add_to_cart("u1", "kettle-01", 1)
    assert cart.lines[0].quantity == 3

    order = await backend.checkout("u1")
    assert order.lines[0].product_id == "kettle-01"
    cart = await backend.get_cart("u1")
    assert cart.lines == []


async def test_carts_are_isolated_per_user() -> None:
    backend = FakeShopBackend()
    await backend.add_to_cart("u1", "kettle-01", 1)
    cart = await backend.get_cart("u2")
    assert cart.lines == []


async def test_search_policies_returns_excerpts() -> None:
    backend = FakeShopBackend()
    policies = await backend.search_policies("returns")
    assert policies and "Returns" in policies[0]
