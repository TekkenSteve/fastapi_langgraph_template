"""Unit tests for the memory extraction node."""

import sys
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.memory import InMemoryStore

from shopping_agent.nodes.extract_memory import _last_exchange_text, extract_memory
from shopping_agent.state import Context, State


class _FakeModel:
    def __init__(self, text: str) -> None:
        self._text = text

    async def ainvoke(self, messages: list) -> Any:
        return AIMessage(content=self._text)


def _runtime(store: Any, *, enabled: bool = True) -> Any:
    return SimpleNamespace(context=Context(enable_memory_extraction=enabled), store=store)


def _state() -> State:
    return State(
        messages=[
            HumanMessage(content="I only like light roast coffee"),
            AIMessage(content="Noted! Light roast it is."),
        ]
    )


def test_last_exchange_text_picks_user_and_answer_only() -> None:
    text = _last_exchange_text(_state())
    assert "light roast" in text
    assert "assistant:" in text


async def test_extracts_and_saves_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys.modules["shopping_agent.nodes.extract_memory"],
        "load_chat_model_with_fallbacks",
        lambda name, _fallbacks=None: _FakeModel('[{"key": "roast", "value": "light"}]'),
    )
    store = InMemoryStore()
    await extract_memory(_state(), {"configurable": {"user_id": "u1"}}, _runtime(store))

    from shared.memory import MemoryStore

    assert await MemoryStore(store).load("u1") == {"roast": "light"}


async def test_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryStore()
    result = await extract_memory(_state(), {"configurable": {}}, _runtime(store, enabled=False))
    assert result == {}
    assert (await store.aget(("preferences",), "demo-user")) is None


async def test_prose_answer_saves_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys.modules["shopping_agent.nodes.extract_memory"],
        "load_chat_model_with_fallbacks",
        lambda name, _fallbacks=None: _FakeModel("nothing to save"),
    )
    store = InMemoryStore()
    await extract_memory(_state(), {"configurable": {"user_id": "u1"}}, _runtime(store))
    assert (await store.aget(("preferences",), "u1")) is None


async def test_filtered_facts_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys.modules["shopping_agent.nodes.extract_memory"],
        "load_chat_model_with_fallbacks",
        lambda name, _fallbacks=None: _FakeModel('[{"key": "card", "value": "4111 1111 1111 1111"}]'),
    )
    store = InMemoryStore()
    await extract_memory(_state(), {"configurable": {"user_id": "u1"}}, _runtime(store))
    assert (await store.aget(("preferences",), "u1")) is None
