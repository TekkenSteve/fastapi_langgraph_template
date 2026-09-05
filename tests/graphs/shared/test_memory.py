"""Unit tests for the memory write filter and lifecycle store."""

import pytest
from langgraph.store.memory import InMemoryStore

from shared.memory import (
    MemoryStore,
    MemoryWriteFilter,
    MemoryWriteRejected,
    validate_fact,
)


def test_validate_fact_normalizes_key() -> None:
    key, value = validate_fact("Roast Level", "light", write_filter=MemoryWriteFilter.build())
    assert key == "roast_level"
    assert value == "light"


@pytest.mark.parametrize(
    "value",
    ["4111 1111 1111 1111", "user@example.com", "DE89370400440532013000"],
)
def test_write_filter_rejects_identifier_shaped_values(value: str) -> None:
    with pytest.raises(MemoryWriteRejected):
        validate_fact("note", value, write_filter=MemoryWriteFilter.build())


def test_write_filter_rejects_oversized_or_empty() -> None:
    with pytest.raises(MemoryWriteRejected):
        validate_fact("", "x", write_filter=MemoryWriteFilter.build())
    key, value = validate_fact("k", "v" * 500, write_filter=MemoryWriteFilter.build())
    assert len(value) == 200


async def test_store_save_load_delete_purge() -> None:
    store = InMemoryStore()
    memory = MemoryStore(store)
    await memory.save("u1", "roast", "light")
    await memory.save("u1", "budget", "under 50")
    assert await memory.load("u1") == {"roast": "light", "budget": "under 50"}

    assert await memory.delete("u1", "roast") is True
    assert await memory.load("u1") == {"budget": "under 50"}

    await memory.purge("u1")
    assert await memory.load("u1") == {}


async def test_load_filters_expired_facts() -> None:
    from datetime import UTC, datetime, timedelta

    store = InMemoryStore()
    memory = MemoryStore(store, retention_days=30)
    await memory.save("u1", "roast", "light")

    # Backdate the fact beyond retention
    item = await store.aget(("preferences",), "u1")
    facts = dict(item.value)
    facts["roast"]["saved_at"] = (datetime.now(tz=UTC) - timedelta(days=31)).isoformat()
    await store.aput(("preferences",), "u1", facts)

    assert await memory.load("u1") == {}


async def test_save_rejected_facts_never_reach_store() -> None:
    store = InMemoryStore()
    memory = MemoryStore(store)
    with pytest.raises(MemoryWriteRejected):
        await memory.save("u1", "card", "4111-1111-1111-1111")
    assert await memory.load("u1") == {}
