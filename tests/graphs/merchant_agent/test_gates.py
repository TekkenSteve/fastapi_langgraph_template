"""Unit tests for merchant_agent gates (pure guardrail/provenance functions)."""

from merchant_agent.gates import check_change_provenance, check_listing_provenance, check_price_move


def test_listing_provenance_allows_read_listing() -> None:
    assert check_listing_provenance(["kettle-01"], "kettle-01") is None


def test_listing_provenance_rejects_unread_listing() -> None:
    error = check_listing_provenance([], "kettle-01")
    assert error is not None and "get_listing" in error


def test_change_provenance() -> None:
    assert check_change_provenance(["change-0001"], "change-0001") is None
    assert check_change_provenance([], "change-0001") is not None


def test_price_move_within_cap() -> None:
    assert check_price_move(100.0, 115.0, max_move_pct=20.0) is None


def test_price_move_over_cap_rejected() -> None:
    error = check_price_move(100.0, 130.0, max_move_pct=20.0)
    assert error is not None and "30.0%" in error


def test_price_move_rejects_non_positive() -> None:
    assert check_price_move(100.0, 0, max_move_pct=20.0) is not None
