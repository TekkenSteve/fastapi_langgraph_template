"""Tests for per-identity rate limiting (auth/rate_limit.py)."""

import pytest
from fastapi import HTTPException

import agent_server.auth.rate_limit as rl
from agent_server.auth.rate_limit import rate_limit_default, rate_limit_runs
from agent_server.domain.user import User


@pytest.fixture(autouse=True)
def _reset_limiter(monkeypatch):
    monkeypatch.setattr(rl, "_limiter", None)
    monkeypatch.setattr(rl.settings.app, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(rl.settings.app, "RATE_LIMIT_DEFAULT", "3/minute")
    monkeypatch.setattr(rl.settings.app, "RATE_LIMIT_RUNS", "2/minute")
    yield


def _request(path="/assistants"):
    req = type("Req", (), {})()
    req.url = type("Url", (), {"path": path})()
    req.client = None
    return req


async def test_allows_within_limit() -> None:
    user = User(identity="alice")
    for _ in range(3):
        await rate_limit_default(_request(), user)  # 3/minute: all pass


async def test_rejects_beyond_limit_with_retry_after() -> None:
    user = User(identity="alice")
    for _ in range(3):
        await rate_limit_default(_request(), user)
    with pytest.raises(HTTPException) as exc:
        await rate_limit_default(_request(), user)
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


async def test_identities_are_isolated() -> None:
    alice = User(identity="alice")
    bob = User(identity="bob")
    for _ in range(3):
        await rate_limit_default(_request(), alice)
    await rate_limit_default(_request(), bob)  # bob unaffected


async def test_runs_tier_is_tighter() -> None:
    user = User(identity="carol")
    for _ in range(2):
        await rate_limit_runs(_request("/threads/t/runs"), user)
    with pytest.raises(HTTPException):
        await rate_limit_runs(_request("/threads/t/runs"), user)


async def test_disabled_is_passthrough(monkeypatch) -> None:
    monkeypatch.setattr(rl.settings.app, "RATE_LIMIT_ENABLED", False)
    user = User(identity="dave")
    for _ in range(10):
        await rate_limit_runs(_request(), user)
