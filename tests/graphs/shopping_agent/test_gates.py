"""Unit tests for shopping_agent business gates."""

from shopping_agent.gates import check_provenance, check_quantity


def test_provenance_allows_searched_product() -> None:
    assert check_provenance(["kettle-01"], "kettle-01") is None


def test_provenance_rejects_unseen_product() -> None:
    error = check_provenance(["kettle-01"], "grinder-01")
    assert error is not None
    assert "grinder-01" in error


def test_quantity_within_cap() -> None:
    assert check_quantity(3, cap=5) is None


def test_quantity_rejects_over_cap() -> None:
    error = check_quantity(6, cap=5)
    assert error is not None
    assert "5" in error


def test_quantity_rejects_non_positive() -> None:
    assert check_quantity(0, cap=5) is not None
