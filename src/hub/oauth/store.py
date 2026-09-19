"""OAuth token store: port and dev/prod backends.

Tokens are ephemeral — TTL is the whole lifecycle. Backend selection mirrors
the SSE broker exactly: in-memory for dev (single process, restart means
re-auth), Redis when the broker is enabled (multi-pod: the browser callback
and the waiting run must see the same store even on different pods).

Durable data (DCR client registrations) does NOT belong here — that lives in
Postgres (hub.db McpOauthClient).
"""

import time
from typing import Protocol

import structlog

from agent_server.config.settings import settings

logger = structlog.getLogger(__name__)


class TokenStore(Protocol):
    """Ephemeral string storage with optional TTL."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, *, ttl_secs: float | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...


class InMemoryTokenStore:
    """Process-local store (dev default). Entries expire lazily on read."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, float | None]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and expires_at <= time.monotonic():
            self._entries.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, *, ttl_secs: float | None = None) -> None:
        expires_at = time.monotonic() + ttl_secs if ttl_secs is not None else None
        self._entries[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        self._entries.pop(key, None)


class RedisTokenStore:
    """Redis-backed store (prod, broker enabled). TTL is native (SETEX)."""

    def __init__(self) -> None:
        from agent_server.infra.redis import redis_manager

        self._client = redis_manager.get_client()

    async def get(self, key: str) -> str | None:
        value = await self._client.get(key)
        # redis.asyncio returns bytes unless decode_responses=True — normalize.
        return value.decode() if isinstance(value, bytes) else value

    async def set(self, key: str, value: str, *, ttl_secs: float | None = None) -> None:
        if ttl_secs is not None:
            await self._client.set(key, value, ex=max(1, int(ttl_secs)))
        else:
            await self._client.set(key, value)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)


_store: TokenStore | None = None


def get_token_store() -> TokenStore:
    """Process-wide store: Redis when the broker is enabled, memory otherwise."""
    global _store
    if _store is None:
        _store = RedisTokenStore() if settings.redis.REDIS_BROKER_ENABLED else InMemoryTokenStore()
        logger.info("oauth_token_store_selected", backend=type(_store).__name__)
    return _store


def reset_token_store() -> None:
    """Drop the cached store (tests and settings reloads)."""
    global _store
    _store = None
