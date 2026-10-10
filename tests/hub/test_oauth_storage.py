"""Unit tests for HubTokenStorage: the SDK's persistence seam.

The Postgres half (DCR registrations) is exercised with a fake session maker —
the real SQL path is covered by the self-skipping DB suite (test_db.py).
"""

from typing import Any

import pytest
from mcp.shared.auth import OAuthToken

import hub.oauth.storage as storage_mod
from hub.oauth.storage import HubTokenStorage
from hub.oauth.store import InMemoryTokenStore


class _FakeSession:
    """Records the statements ``clear()`` issues."""

    def __init__(self) -> None:
        self.executed: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any) -> None:
        self.executed.append(statement)

    async def commit(self) -> None:
        self.commits += 1


class _FakeMaker:
    """Stands in for agent_server's async_sessionmaker."""

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __call__(self) -> "_FakeMaker":
        return self

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def store() -> InMemoryTokenStore:
    return InMemoryTokenStore()


async def test_clear_drops_tokens_and_client_registration(
    store: InMemoryTokenStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a connection must not leave the old endpoint's credentials."""
    storage = HubTokenStorage("u1", "acme-kb", store=store)
    await storage.set_tokens(OAuthToken(access_token="tok", expires_in=3600))
    session = _FakeSession()
    monkeypatch.setattr(storage_mod, "get_session_maker", lambda: _FakeMaker(session))

    await storage.clear()

    assert await storage.get_tokens() is None
    assert session.commits == 1
    assert "DELETE FROM MCP_OAUTH_CLIENT" in str(session.executed[0]).upper()


async def test_tokens_are_routed_to_the_store(store: InMemoryTokenStore) -> None:
    storage = HubTokenStorage("u1", "acme-kb", store=store)
    assert await storage.get_tokens() is None

    await storage.set_tokens(OAuthToken(access_token="tok", expires_in=3600))

    tokens = await storage.get_tokens()
    assert tokens is not None
    assert tokens.access_token == "tok"
