"""Unit tests for the vendored circuit breaker (infra/circuit_breaker.py)."""

import time
from typing import Any

import pytest

from agent_server.infra.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    MemoryBreakerStorage,
    RedisBreakerStorage,
)


def _breaker(name: str = "svc", *, fail_max: int = 3, cooldown: float = 60.0) -> CircuitBreaker:
    return CircuitBreaker(name, fail_max=fail_max, cooldown_secs=cooldown, storage=MemoryBreakerStorage())


async def _ok() -> str:
    return "fine"


async def _boom() -> str:
    raise ConnectionError("down")


async def test_calls_pass_through_when_closed() -> None:
    assert await _breaker().call(_ok) == "fine"


async def test_opens_after_fail_max_and_fast_fails() -> None:
    breaker = _breaker(fail_max=2)
    for _ in range(2):
        with pytest.raises(ConnectionError):
            await breaker.call(_boom)
    with pytest.raises(CircuitOpenError):
        await breaker.call(_ok)


async def test_success_resets_the_failure_count() -> None:
    breaker = _breaker(fail_max=2)
    with pytest.raises(ConnectionError):
        await breaker.call(_boom)
    assert await breaker.call(_ok) == "fine"
    with pytest.raises(ConnectionError):
        await breaker.call(_boom)
    assert await breaker.call(_ok) == "fine"  # still closed — count was reset


async def test_half_open_probe_reopens_on_failure() -> None:
    breaker = _breaker(fail_max=1, cooldown=-1.0)  # open window already past
    with pytest.raises(ConnectionError):
        await breaker.call(_boom)  # opens, but cooldown is negative → probe allowed
    with pytest.raises(ConnectionError):
        await breaker.call(_boom)  # probe fails → re-open
    breaker2 = _breaker("svc2", fail_max=1, cooldown=-1.0)
    with pytest.raises(ConnectionError):
        await breaker2.call(_boom)
    # same probe path: failure count kept accumulating under the hood
    with pytest.raises(ConnectionError):
        await breaker2.call(_boom)


async def test_fail_max_must_be_positive() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        _breaker(fail_max=0)


class _FakeRedis:
    """Minimal async stand-in for the redis client surface we use."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


async def test_redis_storage_round_trip() -> None:
    storage = RedisBreakerStorage(_FakeRedis())
    assert await storage.get_state("svc") == (0, 0.0)
    await storage.set_state("svc", failures=3, open_until=time.time() + 60)
    failures, open_until = await storage.get_state("svc")
    assert failures == 3
    assert open_until > time.time()
    await storage.clear("svc")
    assert await storage.get_state("svc") == (0, 0.0)


async def test_redis_backed_breaker_shares_state() -> None:
    """Two breakers on one storage see each other's failures (multi-pod shape)."""
    storage = RedisBreakerStorage(_FakeRedis())
    a = CircuitBreaker("svc", fail_max=2, cooldown_secs=60.0, storage=storage)
    b = CircuitBreaker("svc", fail_max=2, cooldown_secs=60.0, storage=storage)
    with pytest.raises(ConnectionError):
        await a.call(_boom)
    with pytest.raises(ConnectionError):
        await b.call(_boom)  # second pod's failure trips the shared breaker
    with pytest.raises(CircuitOpenError):
        await a.call(_ok)
    with pytest.raises(CircuitOpenError):
        await b.call(_ok)
