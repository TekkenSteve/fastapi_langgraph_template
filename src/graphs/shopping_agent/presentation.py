"""Presentation components for the shopping agent (generative UI).

present_products renders a product carousel; present_comparison a comparison
grid; present_checkout_summary a checkout recap; present_suggestions renders
the turn's suggestion chips. All validate the model's arguments, then join
every fact from the backend — the model selects ids and writes short reasons,
it never writes names or prices.
"""

from pydantic import Field

from shared.presentation import (
    EnrichmentContext,
    PresentationComponent,
    PresentationPayload,
    PresentationRefused,
    SuggestionsPayload,
)


class PresentProductsPayload(PresentationPayload):
    product_ids: list[str] = Field(min_length=1, max_length=6)
    reasons: list[str] = Field(default_factory=list, max_length=6)


class PresentComparisonPayload(PresentationPayload):
    product_ids: list[str] = Field(min_length=2, max_length=4)


class CheckoutSummaryPayload(PresentationPayload):
    """No fields: the summary is assembled from the server-side cart only."""


async def _join_products(payload: PresentationPayload, context: EnrichmentContext, *, reasons: list[str]) -> list[dict]:
    assert isinstance(payload, PresentProductsPayload | PresentComparisonPayload)
    products = []
    for index, product_id in enumerate(payload.product_ids):
        if product_id not in context.seen_ids:
            context.notes.append(f"{product_id} was dropped — it was not returned by search this session.")
            continue
        product = await context.backend.get_product(product_id)
        if product is None:
            context.notes.append(f"{product_id} was dropped — not in the catalog.")
            continue
        products.append(
            {
                "id": product.id,
                "name": product.name,
                "price": product.price,
                "description": product.description,
                "reason": reasons[index] if index < len(reasons) else "",
            }
        )
    if not products:
        raise PresentationRefused("nothing renderable: every product id lacked session provenance.", gate="provenance")
    return products


async def enrich_products(payload: PresentationPayload, context: EnrichmentContext) -> dict:
    assert isinstance(payload, PresentProductsPayload)
    return {"products": await _join_products(payload, context, reasons=payload.reasons)}


async def enrich_comparison(payload: PresentationPayload, context: EnrichmentContext) -> dict:
    assert isinstance(payload, PresentComparisonPayload)
    products = await _join_products(payload, context, reasons=[])
    if len(products) < 2:
        raise PresentationRefused(
            "a comparison needs at least two products with session provenance.", gate="provenance"
        )
    return {"products": products}


async def enrich_checkout_summary(payload: PresentationPayload, context: EnrichmentContext) -> dict:
    """The recap is assembled from the server-side cart only — no model facts."""
    cart = await context.backend.get_cart(context.user_id)
    if not cart.lines:
        raise PresentationRefused("the cart is empty — nothing to summarize.", gate="empty_cart")
    products = []
    total = 0.0
    for line in cart.lines:
        product = await context.backend.get_product(line.product_id)
        if product is None:
            continue
        total += product.price * line.quantity
        products.append({"id": product.id, "name": product.name, "price": product.price, "quantity": line.quantity})
    return {"lines": products, "total": round(total, 2), "currency": "USD"}


PRESENT_PRODUCTS = PresentationComponent(
    name="present_products",
    component="ProductCarousel",
    payload_model=PresentProductsPayload,
    enrich=enrich_products,
)

PRESENT_COMPARISON = PresentationComponent(
    name="present_comparison",
    component="ComparisonGrid",
    payload_model=PresentComparisonPayload,
    enrich=enrich_comparison,
)

PRESENT_CHECKOUT_SUMMARY = PresentationComponent(
    name="present_checkout_summary",
    component="CheckoutSummary",
    payload_model=CheckoutSummaryPayload,
    enrich=enrich_checkout_summary,
)

PRESENT_SUGGESTIONS = PresentationComponent(
    name="present_suggestions",
    component="suggestions",
    payload_model=SuggestionsPayload,
)
