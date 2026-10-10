"""Unit tests for client-config sanitization (strip_pinned_config_keys)."""

from agent_server.domain.run_config import (
    SERVER_PINNED_CONFIG_KEYS,
    SERVER_PINNED_IDENTITY_KEYS,
    configurable_user_id,
    strip_pinned_config_keys,
)


class TestStripPinnedConfigKeys:
    def test_strips_thread_id_and_run_id(self) -> None:
        """Server-authoritative identity keys must be dropped from client input.

        A client checkpoint carrying thread_id would otherwise override the
        ownership-verified thread and redirect state reads/writes (GHSA cross
        -tenant class).
        """
        cleaned = strip_pinned_config_keys({"thread_id": "victim", "run_id": "victim-run", "checkpoint_id": "cp-1"})

        assert "thread_id" not in cleaned
        assert "run_id" not in cleaned

    def test_preserves_legitimate_checkpoint_keys(self) -> None:
        cleaned = strip_pinned_config_keys({"checkpoint_id": "cp-1", "checkpoint_ns": "ns"})

        assert cleaned == {"checkpoint_id": "cp-1", "checkpoint_ns": "ns"}

    def test_empty_dict_returns_empty(self) -> None:
        assert strip_pinned_config_keys({}) == {}

    def test_strips_the_caller_identity(self) -> None:
        """A client-supplied user_id would let a run act as another tenant.

        Skills, MCP connections and graph tools all resolve the acting user
        from the run config, so this is a cross-tenant read, not a display bug.
        """
        cleaned = strip_pinned_config_keys(
            {
                "user_id": "victim",
                "user_display_name": "Victim",
                "langgraph_auth_user": {"identity": "victim"},
                "checkpoint_id": "cp-1",
            }
        )

        assert cleaned == {"checkpoint_id": "cp-1"}

    def test_identity_only_strip_keeps_routing_keys(self) -> None:
        """The run context has no defined routing keys, so only identity is dropped."""
        cleaned = strip_pinned_config_keys(
            {"user_id": "victim", "langgraph_auth_user": {"identity": "victim"}, "temperature": 0.2},
            keys=SERVER_PINNED_IDENTITY_KEYS,
        )

        assert cleaned == {"temperature": 0.2}
        assert "thread_id" not in SERVER_PINNED_IDENTITY_KEYS


class TestConfigurableUserId:
    def test_prefers_the_server_injected_auth_user(self) -> None:
        """The auth user wins over a spoofed configurable.user_id."""
        user = type("U", (), {"identity": "real-user"})()

        resolved = configurable_user_id({"configurable": {"user_id": "victim", "langgraph_auth_user": user}})

        assert resolved == "real-user"

    def test_falls_back_to_user_id_for_configs_without_an_auth_user(self) -> None:
        assert configurable_user_id({"configurable": {"user_id": "u1"}}) == "u1"

    def test_none_when_no_identity_is_present(self) -> None:
        assert configurable_user_id({}) is None
        assert configurable_user_id({"configurable": {"thread_id": "t1"}}) is None
        assert configurable_user_id({"configurable": "not-a-mapping"}) is None

    def test_identity_keys_are_all_pinned(self) -> None:
        assert {"thread_id", "run_id", "user_id", "user_display_name", "langgraph_auth_user"} <= (
            SERVER_PINNED_CONFIG_KEYS
        )
