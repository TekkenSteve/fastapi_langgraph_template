"""OAuth flow plumbing: build SDK auth providers and correlate browser callbacks.

The SDK (OAuthClientProvider) drives the flow; this module is the server-side
glue for a browser that is *not* on this machine:

1. SDK builds the authorize URL → our ``redirect_handler`` stores it
   (latest wins — see on_redirect for why) and indexes its ``state``
   so the callback endpoint can find the owner.
2. We raise a LangGraph ``interrupt`` with the connect_url — the run pauses;
   the payload reaches the caller via the Agent Protocol HITL channel.
3. The user authorizes in their browser; the auth server redirects to our
   public ``/hub/oauth/callback`` endpoint, which stores the code.
4. The caller resumes the run; ``callback_handler`` finds the code and hands
   it to the SDK, which exchanges it for tokens (stored via HubTokenStorage)
   and the tool call proceeds.

One outstanding flow per (user, connection) — a fine granularity for an
agent run, and it keeps the callback correlation trivial.
"""

import json
from urllib.parse import parse_qs, urlparse

import structlog
from langgraph.types import interrupt
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata

from agent_server.config.settings import settings
from hub.oauth.storage import HubTokenStorage
from hub.oauth.store import TokenStore, get_token_store

logger = structlog.getLogger(__name__)

_PENDING_TTL_SECS = 600.0
_CODE_TTL_SECS = 300.0


def _pending_key(user_id: str, connection: str) -> str:
    return f"mcp_oauth:pending:{user_id}:{connection}"


def _code_key(user_id: str, connection: str) -> str:
    return f"mcp_oauth:code:{user_id}:{connection}"


def _state_key(state: str) -> str:
    return f"mcp_oauth:state:{state}"


def callback_base_url() -> str:
    """Public base for the OAuth redirect back into this server."""
    base = settings.app.SERVER_URL or f"http://localhost:{settings.app.PORT}"
    return base.rstrip("/")


class OAuthFlowCorrelator:
    """One user's outstanding OAuth flow for one connection."""

    def __init__(self, user_id: str, connection_name: str, *, store: TokenStore | None = None) -> None:
        self._user_id = user_id
        self._connection_name = connection_name
        self._store = store or get_token_store()

    async def on_redirect(self, url: str) -> None:
        """SDK redirect_handler: park the authorize URL for the interrupt.

        Always the LATEST URL: a resumed run re-enters the SDK flow, which
        mints a fresh state — and the SDK later requires the returned code to
        come back with *its current* state. The code itself stays parked
        across attempts, so a browser flow completed against an earlier URL
        still resolves the retry.
        """
        state = parse_qs(urlparse(url).query).get("state", [None])[0]
        if state is None:
            logger.warning("mcp_oauth_redirect_without_state", connection=self._connection_name)
            return
        await self._store.set(
            _pending_key(self._user_id, self._connection_name),
            json.dumps({"url": url, "state": state}),
            ttl_secs=_PENDING_TTL_SECS,
        )
        await self._store.set(
            _state_key(state),
            json.dumps({"user_id": self._user_id, "connection": self._connection_name}),
            ttl_secs=_PENDING_TTL_SECS,
        )

    async def await_code(self) -> tuple[str, str | None]:
        """SDK callback_handler: hand over the code, or pause the run for it."""
        pending_key = _pending_key(self._user_id, self._connection_name)
        pending = await self._store.get(pending_key)
        code = await self._store.get(_code_key(self._user_id, self._connection_name))
        if code is not None:
            state = json.loads(pending)["state"] if pending else None
            await self._store.delete(_code_key(self._user_id, self._connection_name))
            await self._store.delete(pending_key)
            return code, state

        connect_url = json.loads(pending)["url"] if pending else None
        payload = json.dumps(
            {
                "type": "mcp_auth_required",
                "mcp_name": self._connection_name,
                "connect_url": connect_url,
                "message": f"Connect to {self._connection_name} to use these tools",
            }
        )
        # Inside a graph run this pauses at the interrupt and resumes here once
        # the caller resumes the thread — by which time the browser callback
        # should have landed the code (the run then retries the tool call).
        raise interrupt(payload)


async def complete_from_browser(state: str, code: str) -> bool:
    """Callback route entry: park a browser-delivered code by its state.

    Returns False when the state is unknown or expired (stale/double
    callback), so the route can answer 400 instead of parking an orphan code.
    """
    store = get_token_store()
    raw = await store.get(_state_key(state))
    if raw is None:
        return False
    await store.delete(_state_key(state))
    ctx = json.loads(raw)
    await store.set(_code_key(ctx["user_id"], ctx["connection"]), code, ttl_secs=_CODE_TTL_SECS)
    logger.info("mcp_oauth_code_received", connection=ctx["connection"])
    return True


def build_oauth_auth(user_id: str, connection_name: str, server_url: str) -> OAuthClientProvider:
    """An SDK OAuthClientProvider wired to hub storage and the interrupt UX."""
    correlator = OAuthFlowCorrelator(user_id, connection_name)
    metadata = OAuthClientMetadata(
        client_name=f"agent-server ({connection_name})",
        redirect_uris=[f"{callback_base_url()}/hub/oauth/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )
    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=HubTokenStorage(user_id, connection_name),
        redirect_handler=correlator.on_redirect,
        callback_handler=correlator.await_code,
    )
