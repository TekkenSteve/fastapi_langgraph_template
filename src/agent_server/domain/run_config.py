"""Run config policy: which keys are server-authoritative."""

from typing import Any

# Identity keys the server pins; clients may not override them.
SERVER_PINNED_CONFIG_KEYS: frozenset[str] = frozenset({"thread_id", "run_id"})


def strip_pinned_config_keys(client_config: dict[str, Any]) -> dict[str, Any]:
    """Drop server-authoritative identity keys from a client-supplied config dict."""
    return {k: v for k, v in client_config.items() if k not in SERVER_PINNED_CONFIG_KEYS}
