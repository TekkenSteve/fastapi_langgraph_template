"""Asyncio-native circuit breaker with pluggable storage.

Why vendored (the evaluation trail, kept for future readers):
- pybreaker's async path is a tornado shim — wrong fit for an asyncio service.
- aiobreaker is asyncio-native but unmaintained since ~2021: it uses
  ``datetime.utcnow()`` (deprecated, slated for removal) and ships no
  ``py.typed``. A ~50-line state machine should not carry a zombie dependency.

So the state machine lives here, typed and owned. Storage is the only seam:
process memory by default, shared Redis (our *async* client — no sync-redis
quirk) when the broker is enabled, so one pod's failures protect the others.
"""

import time
from typing import Any, Protocol

import structlog

logger = structlog.getLogger(__name__)


class CircuitOpenError(Exception):
    """The breaker is open; the call was not attempted."""


class BreakerStorage(Protocol):
    """Where breaker state lives. Wall-clock timestamps (shareable across pods)."""

    async def get_state(self, name: str) -> tuple[int, float]: ...
    async def set_state(self, name: str, *, failures: int, open_until: float) -> None: ...
    async def clear(self, name: str) -> None: ...


class MemoryBreakerStorage:
    """Process-local state (default; single-pod semantics)."""

    def __init__(self) -> None:
        self._state: dict[str, tuple[int, float]] = {}

    async def get_state(self, name: str) -> tuple[int, float]:
        return self._state.get(name, (0, 0.0))

    async def set_state(self, name: str, *, failures: int, open_until: float) -> None:
        self._state[name] = (failures, open_until)

    async def clear(self, name: str) -> None:
        self._state.pop(name, None)


class RedisBreakerStorage:
    """Shared state in Redis (multi-pod). Values expire with the cooldown so
    stale entries self-clean; races are harmless at breaker semantics."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    @staticmethod
    def _key(name: str) -> str:
        return f"circuit_breaker:{name}"

    async def get_state(self, name: str) -> tuple[int, float]:
        raw = await self._redis.get(self._key(name))
        if not raw:
            return 0, 0.0
        failures, _, open_until = raw.partition(":")
        return int(failures), float(open_until)

    async def set_state(self, name: str, *, failures: int, open_until: float) -> None:
        ttl = max(1, int(open_until - time.time())) if open_until > time.time() else 1
        await self._redis.set(self._key(name), f"{failures}:{open_until}", ex=ttl)

    async def clear(self, name: str) -> None:
        await self._redis.delete(self._key(name))


class CircuitBreaker:
    """fail_max consecutive failures → open for cooldown_secs; the first call
    after the cooldown is a probe that re-opens on failure (half-open)."""

    def __init__(self, name: str, *, fail_max: int, cooldown_secs: float, storage: BreakerStorage) -> None:
        if fail_max < 1:
            raise ValueError("fail_max must be >= 1")
        self._name = name
        self._fail_max = fail_max
        self._cooldown_secs = cooldown_secs
        self._storage = storage

    async def state_snapshot(self) -> dict[str, Any]:
        """Read-only view for diagnostics: failures, open, seconds remaining."""
        failures, open_until = await self._storage.get_state(self._name)
        remaining = max(0.0, open_until - time.time())
        return {
            "failures": failures,
            "open": open_until > time.time(),
            "open_for_secs": round(remaining, 1),
        }

    async def call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run ``func`` unless the breaker is open."""
        _, open_until = await self._storage.get_state(self._name)
        if open_until > time.time():
            raise CircuitOpenError(f"circuit open for {self._name}")
        try:
            result = await func(*args, **kwargs)
        except Exception:
            failures, _ = await self._storage.get_state(self._name)
            failures += 1
            new_open_until = 0.0
            if failures >= self._fail_max or open_until > 0.0:
                # Threshold reached, or the half-open probe failed — re-open.
                new_open_until = time.time() + self._cooldown_secs
                logger.warning("circuit_breaker_opened", name=self._name, failures=failures)
            await self._storage.set_state(self._name, failures=failures, open_until=new_open_until)
            raise
        await self._storage.clear(self._name)
        return result


_memory_storage = MemoryBreakerStorage()
_redis_storage: RedisBreakerStorage | None = None


def get_breaker_storage(*, redis_client: Any = None) -> BreakerStorage:
    """Shared Redis storage when a client is supplied, else process memory."""
    global _redis_storage
    if redis_client is None:
        return _memory_storage
    if _redis_storage is None:
        _redis_storage = RedisBreakerStorage(redis_client)
    return _redis_storage


def reset_breaker_state() -> None:
    """Drop cached storage (tests; not needed at runtime)."""
    global _memory_storage, _redis_storage
    _memory_storage = MemoryBreakerStorage()
    _redis_storage = None
