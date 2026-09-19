"""HubTokenStorage: MCP SDK TokenStorage over hub backends.

The SDK's OAuthClientProvider owns the entire OAuth state machine (PKCE,
authorization code grant, refresh, DCR); this class is only the persistence
seam. It routes the two record kinds by lifetime:

- tokens (ephemeral) → TokenStore (memory dev / Redis prod), TTL from the
  token's own expires_in;
- client_info (durable DCR registration) → Postgres mcp_oauth_client
  (EncryptedJson — client_secret never sits in plaintext).
"""

import structlog
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import select

from agent_server.repo.orm import get_session_maker
from hub.db import McpOauthClient as McpOauthClientORM
from hub.oauth.store import TokenStore, get_token_store

logger = structlog.getLogger(__name__)

# Refresh slack: treat the token as gone slightly before its real expiry.
_EXPIRY_SKEW_SECS = 60.0
# Tokens without expires_in get a conservative bound anyway.
_DEFAULT_TOKEN_TTL_SECS = 3600.0


class HubTokenStorage:
    """SDK TokenStorage for one (user, connection) pair."""

    def __init__(self, user_id: str, connection_name: str, *, store: TokenStore | None = None) -> None:
        self._user_id = user_id
        self._connection_name = connection_name
        self._store = store or get_token_store()

    @property
    def _tokens_key(self) -> str:
        return f"mcp_oauth:tok:{self._user_id}:{self._connection_name}"

    async def get_tokens(self) -> OAuthToken | None:
        raw = await self._store.get(self._tokens_key)
        if raw is None:
            return None
        return OAuthToken.model_validate_json(raw)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        ttl = (tokens.expires_in - _EXPIRY_SKEW_SECS) if tokens.expires_in else _DEFAULT_TOKEN_TTL_SECS
        await self._store.set(self._tokens_key, tokens.model_dump_json(), ttl_secs=max(ttl, 1.0))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        maker = get_session_maker()
        async with maker() as session:
            row = (
                await session.execute(
                    select(McpOauthClientORM).where(
                        McpOauthClientORM.user_id == self._user_id,
                        McpOauthClientORM.connection_name == self._connection_name,
                    )
                )
            ).scalar_one_or_none()
        if row is None or row.registration is None:
            return None
        return OAuthClientInformationFull.model_validate(row.registration)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        maker = get_session_maker()
        async with maker() as session:
            row = (
                await session.execute(
                    select(McpOauthClientORM).where(
                        McpOauthClientORM.user_id == self._user_id,
                        McpOauthClientORM.connection_name == self._connection_name,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = McpOauthClientORM(user_id=self._user_id, connection_name=self._connection_name)
                session.add(row)
            row.registration = client_info.model_dump(mode="json")
            await session.commit()
        logger.info("mcp_oauth_client_registered", user_id=self._user_id, connection=self._connection_name)
