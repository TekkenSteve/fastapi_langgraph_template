"""The unit/integration/e2e markers are derived from the test directory.

Derivation lives in ``tests/conftest.py``; if that hook stops running, `-m unit`
and `-m integration` quietly select the wrong set (or nothing) — the false-green
this file exists to prevent. Running here means the hook ran for a real test.
"""

import pytest


def test_the_unit_tier_is_derived_from_the_directory(request: pytest.FixtureRequest) -> None:
    markers = {marker.name for marker in request.node.iter_markers()}

    assert "unit" in markers
    assert "integration" not in markers
