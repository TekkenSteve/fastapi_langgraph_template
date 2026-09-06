"""Shop REST API — merged into the server via the http.app key in
langgraph.json.

This REST surface and the agent (graphs/shopping_agent) share the same
backend port: a product added through the agent's tools is visible here and
vice versa. Add your product-facing endpoints in your domain package instead
of modifying src/agent_server/.

User-scoped endpoints take the identity from the server's auth dependency
(``require_auth``) — never from client-supplied parameters.
"""

from fastapi import Depends, FastAPI, Query

from agent_server.auth.deps import require_auth
from agent_server.domain.user import User
from shop.backends import get_backend

app = FastAPI(title="Shop API")


@app.get("/shop/products")
async def list_products(q: str = Query(default="")) -> list[dict]:
    """Public catalog — no auth needed."""
    products = await get_backend().search_products(q, limit=50)
    return [{"id": p.id, "name": p.name, "price": p.price, "description": p.description} for p in products]


@app.get("/shop/cart")
async def get_cart(user: User = Depends(require_auth)) -> dict:
    """The caller's own cart. Identity comes from the server's auth layer."""
    cart = await get_backend().get_cart(user.identity)
    return {"lines": [vars(line) for line in cart.lines]}
