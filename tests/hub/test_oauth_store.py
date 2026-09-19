"""Unit tests for the OAuth TokenStore backends and factory."""

import pytest

import hub.oauth.store as store_mod
from hub.oauth.store import InMemoryTokenStore, RedisTokenStore, get_token_store, reset_token_store


@pytest.fixture(autouse=True)
def _reset():
    reset_token_store()
    yield
    reset_token_store()


async def test_memory_store_round_trip() -> None:
    store = InMemoryTokenStore()
    await store.set("k", "v")
    assert await store.get("k") == "v"
    await store.delete("k")
    assert await store.get("k") is None


async def test_memory_store_ttl_expires() -> None:
    store = InMemoryTokenStore()
    await store.set("k", "v", ttl_secs=-1.0)
    assert await store.get("k") is None


async def test_factory_defaults_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_mod.settings.redis, "REDIS_BROKER_ENABLED", False)
    assert isinstance(get_token_store(), InMemoryTokenStore)


async def test_factory_selects_redis_when_broker_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr(store_mod.settings.redis, "REDIS_BROKER_ENABLED", True)
    monkeypatch.setattr("agent_server.infra.redis.redis_manager", MagicMock())
    assert isinstance(get_token_store(), RedisTokenStore)
