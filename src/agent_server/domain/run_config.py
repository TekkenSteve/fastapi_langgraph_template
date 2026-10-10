"""Run config policy: which keys are server-authoritative."""

from collections.abc import Mapping
from typing import Any

# Identity and routing keys the server pins; clients may not override them.
#
# ``user_id`` / ``user_display_name`` / ``langgraph_auth_user`` are the caller's
# identity: skills, MCP connections and graph tools all resolve the *acting
# user* from the run config, so a body-supplied value would let one tenant run
# as another. ``thread_id`` / ``run_id`` route execution and identify the root
# run.
SERVER_PINNED_IDENTITY_KEYS: frozenset[str] = frozenset({"user_id", "user_display_name", "langgraph_auth_user"})
SERVER_PINNED_CONFIG_KEYS: frozenset[str] = SERVER_PINNED_IDENTITY_KEYS | {"thread_id", "run_id"}


def strip_pinned_config_keys(
    client_config: Mapping[str, Any], *, keys: frozenset[str] = SERVER_PINNED_CONFIG_KEYS
) -> dict[str, Any]:
    """Drop server-authoritative keys from a client-supplied dict.

    ``keys`` defaults to the full config set (identity + routing); pass
    ``SERVER_PINNED_IDENTITY_KEYS`` for dicts where routing keys have no defined
    meaning (the run ``context``).
    """
    return {k: v for k, v in client_config.items() if k not in keys}


def configurable_user_id(config: Mapping[str, Any] | None) -> str | None:
    """The acting user of a run, read from the server-injected identity.

    ``langgraph_auth_user`` is written by ``inject_user_context`` on every run
    and is the authority. ``configurable.user_id`` is only a fallback for
    configs built outside the request path (smoke scripts, unit tests); it must
    never be the source when the auth user is present.
    """
    configurable = (config or {}).get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    identity = getattr(configurable.get("langgraph_auth_user"), "identity", None)
    if isinstance(identity, str) and identity:
        return identity
    user_id = configurable.get("user_id")
    return user_id if isinstance(user_id, str) and user_id else None
