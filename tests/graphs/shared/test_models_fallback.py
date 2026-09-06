"""Fallback chain loading (shared/models.py)."""

import shared.models as m
from shared.models import load_chat_model_with_fallbacks


def test_no_fallbacks_returns_plain_model(monkeypatch) -> None:
    monkeypatch.setattr(m, "load_chat_model", lambda name: f"model:{name}")
    assert load_chat_model_with_fallbacks("openai/gpt-4o-mini", []) == "model:openai/gpt-4o-mini"


def test_fallbacks_build_chain_in_order(monkeypatch) -> None:
    class _Fake:
        def __init__(self, name):
            self.name = name

        def with_fallbacks(self, others):
            return ("chain", self.name, tuple(others))

    monkeypatch.setattr(m, "load_chat_model", lambda name: _Fake(name))
    chain = load_chat_model_with_fallbacks("openai/gpt-4o", ["openai/gpt-4o-mini", "anthropic/claude-haiku"])
    assert chain[1] == "openai/gpt-4o"
    assert [f.name for f in chain[2]] == ["openai/gpt-4o-mini", "anthropic/claude-haiku"]
