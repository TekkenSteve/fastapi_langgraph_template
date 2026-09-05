"""Composed agents construct their model client at build time, so graph unit
tests need a key-shaped placeholder. Unit tests never make network calls.
"""

import pytest


@pytest.fixture(autouse=True)
def _dummy_llm_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
