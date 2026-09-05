"""Business gates enforced before cart writes.

Gates are pure functions: they return an error string when a write is
rejected, ``None`` when it may proceed. Tools run them before touching the
backend, so no rule depends on prompt wording alone.
"""

from collections.abc import Sequence


def check_provenance(seen_product_ids: Sequence[str], product_id: str) -> str | None:
    """Cart writes only accept product ids returned by search this session."""
    if product_id in seen_product_ids:
        return None
    return (
        f"product_id '{product_id}' was not returned by search_products in this "
        "session. Call search_products first and use an id from its results."
    )


def check_quantity(quantity: int, cap: int) -> str | None:
    """Reject non-positive quantities and quantities above the configured cap."""
    if quantity <= 0:
        return f"quantity must be positive, got {quantity}."
    if quantity > cap:
        return f"quantity {quantity} exceeds the per-line limit of {cap}."
    return None
