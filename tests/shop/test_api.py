"""Unit tests for the shop REST surface (shared backend port)."""

from fastapi.testclient import TestClient

from shop.api import app


def test_list_products_returns_catalog() -> None:
    client = TestClient(app)
    response = client.get("/shop/products")
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()}
    assert "coffee-maker-01" in ids


def test_list_products_filters_by_query() -> None:
    client = TestClient(app)
    response = client.get("/shop/products", params={"q": "kettle"})
    ids = {p["id"] for p in response.json()}
    assert ids == {"kettle-01"}


def test_cart_scoped_to_authenticated_identity() -> None:
    """The cart endpoint ignores client-supplied user ids — identity comes
    from the server's auth layer (anonymous when AUTH_TYPE=noop)."""
    client = TestClient(app)
    response = client.get("/shop/cart", params={"user_id": "somebody-else"})
    assert response.status_code == 200
    assert response.json() == {"lines": []}
