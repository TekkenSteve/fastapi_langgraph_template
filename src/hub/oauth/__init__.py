"""Hub OAuth: per-user MCP authorization built on the MCP SDK's
OAuthClientProvider. The SDK owns the protocol state machine; this package
owns persistence (TokenStore / Postgres DCR) and the browser-interrupt UX."""
