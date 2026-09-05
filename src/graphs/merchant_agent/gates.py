"""Business gates for merchant writes — pure functions, enforced before staging.

Two rule families, following commerce-agents:
- provenance: ids must come from tool results in this session
- guardrails: config-capped limits on what a change may do
"""


def check_listing_provenance(seen_listing_ids: list[str], product_id: str) -> str | None:
    """A staged change only accepts listings read via get_listing this session."""
    if product_id in seen_listing_ids:
        return None
    return (
        f"product_id '{product_id}' was not read via get_listing in this session. "
        "Read the listing first, then stage the change."
    )


def check_change_provenance(staged_change_ids: list[str], change_id: str) -> str | None:
    """apply/discard only accept change ids staged in this session."""
    if change_id in staged_change_ids:
        return None
    return f"change '{change_id}' was not staged in this session."


def check_price_move(old_price: float, new_price: float, max_move_pct: float) -> str | None:
    """Guardrail: cap the size of a price move (in either direction)."""
    if new_price <= 0:
        return f"price must be positive, got {new_price}."
    move_pct = abs(new_price - old_price) / old_price * 100
    if move_pct > max_move_pct:
        return (
            f"price move {move_pct:.1f}% ({old_price} → {new_price}) exceeds the "
            f"configured limit of {max_move_pct:.1f}%."
        )
    return None
