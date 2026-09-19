"""Unit tests for the OAuth flow correlator and browser callback."""

import json

import pytest
from mcp.shared.auth import OAuthToken

import hub.oauth.flow as flow_mod
from hub.oauth.flow import OAuthFlowCorrelator, complete_from_browser
from hub.oauth.storage import HubTokenStorage
from hub.oauth.store import InMemoryTokenStore, reset_token_store


@pytest.fixture(autouse=True)
def _reset():
    reset_token_store()
    yield
    reset_token_store()


def _correlator(store: InMemoryTokenStore, user: str = "u1", name: str = "acme-kb") -> OAuthFlowCorrelator:
    return OAuthFlowCorrelator(user, name, store=store)


_AUTH_URL = "https://auth.example.com/authorize?client_id=c1&state=state-123&redirect_uri=cb"


async def test_redirect_parks_url_and_state_index() -> None:
    store = InMemoryTokenStore()
    c = _correlator(store)
    await c.on_redirect(_AUTH_URL)

    pending = json.loads(await store.get("mcp_oauth:pending:u1:acme-kb"))
    assert pending["state"] == "state-123"
    assert pending["url"] == _AUTH_URL
    indexed = json.loads(await store.get("mcp_oauth:state:state-123"))
    assert indexed == {"user_id": "u1", "connection": "acme-kb"}


async def test_redirect_replaces_pending_with_the_latest_url() -> None:
    """A resumed run re-enters the SDK flow, which mints a fresh state — and
    the SDK demands the code come back with its *current* state, so pending
    must track the latest URL. The parked code survives across attempts."""
    store = InMemoryTokenStore()
    c = _correlator(store)
    await c.on_redirect(_AUTH_URL)
    await c.on_redirect(_AUTH_URL.replace("state-123", "state-999"))

    pending = json.loads(await store.get("mcp_oauth:pending:u1:acme-kb"))
    assert pending["state"] == "state-999"
    # ...and a code parked via the earlier state still resolves the retry
    await store.set("mcp_oauth:code:u1:acme-kb", "code-abc")
    code, state = await c.await_code()
    assert (code, state) == ("code-abc", "state-999")


async def test_await_code_pauses_when_no_code() -> None:
    """Outside a graph, langgraph's interrupt surfaces as RuntimeError;
    inside a run it is the GraphInterrupt that pauses execution."""
    store = InMemoryTokenStore()
    c = _correlator(store)
    await c.on_redirect(_AUTH_URL)
    with pytest.raises(RuntimeError, match="runnable context"):
        await c.await_code()


async def test_await_code_returns_code_and_cleans_up() -> None:
    store = InMemoryTokenStore()
    c = _correlator(store)
    await c.on_redirect(_AUTH_URL)
    await store.set("mcp_oauth:code:u1:acme-kb", "code-abc")

    code, state = await c.await_code()
    assert (code, state) == ("code-abc", "state-123")
    assert await store.get("mcp_oauth:code:u1:acme-kb") is None
    assert await store.get("mcp_oauth:pending:u1:acme-kb") is None


async def test_complete_from_browser_parks_code_by_state(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryTokenStore()
    # complete_from_browser resolves the process-wide store — point it at ours.
    monkeypatch.setattr(flow_mod, "get_token_store", lambda: store)
    c = _correlator(store)
    await c.on_redirect(_AUTH_URL)

    assert await complete_from_browser("state-123", "code-abc")
    assert await store.get("mcp_oauth:code:u1:acme-kb") == "code-abc"
    assert await store.get("mcp_oauth:state:state-123") is None  # single-use


async def test_complete_from_browser_rejects_unknown_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flow_mod, "get_token_store", InMemoryTokenStore)
    assert not await complete_from_browser("nope", "code-abc")


async def test_token_storage_routes_tokens_to_store() -> None:
    store = InMemoryTokenStore()
    storage = HubTokenStorage("u1", "acme-kb", store=store)
    assert await storage.get_tokens() is None

    await storage.set_tokens(OAuthToken(access_token="tok", expires_in=3600))
    tokens = await storage.get_tokens()
    assert tokens is not None
    assert tokens.access_token == "tok"
