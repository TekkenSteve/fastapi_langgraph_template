"""Unit tests for the Phoenix observability target."""

from unittest.mock import patch

from agent_server.infra.observability.targets.phoenix import PhoenixTarget


class TestPhoenixTarget:
    """Tests for PhoenixTarget configuration and exporter creation.

    Exporter assertions target the constructor arguments we pass, not
    attributes of OTLPSpanExporter: OpenTelemetry moved headers off the
    exporter in 1.45, and its internals are not a contract.
    """

    def test_target_name(self):
        """Test that the target has the correct friendly name."""
        target = PhoenixTarget()
        assert target.name == "Phoenix"

    def test_get_exporter_defaults(self):
        """Test exporter is None when endpoint is missing."""
        with patch("agent_server.infra.observability.targets.phoenix.settings") as mock_settings:
            # Simulate no explicit endpoint or API key
            mock_settings.observability.PHOENIX_COLLECTOR_ENDPOINT = None
            mock_settings.observability.PHOENIX_API_KEY = None

            target = PhoenixTarget()
            exporter = target.get_exporter()

            assert exporter is None

    def test_get_exporter_with_custom_endpoint(self):
        """Test exporter creation with a specific collector endpoint."""
        custom_endpoint = "http://phoenix-server:6006/v1/traces"

        with (
            patch("agent_server.infra.observability.targets.phoenix.settings") as mock_settings,
            patch("agent_server.infra.observability.targets.phoenix.OTLPSpanExporter") as mock_exporter_cls,
        ):
            mock_settings.observability.PHOENIX_COLLECTOR_ENDPOINT = custom_endpoint
            mock_settings.observability.PHOENIX_API_KEY = None

            target = PhoenixTarget()
            exporter = target.get_exporter()

            assert exporter is mock_exporter_cls.return_value
            mock_exporter_cls.assert_called_once_with(endpoint=custom_endpoint, headers={})

    def test_get_exporter_with_api_key(self):
        """Test that API key is correctly added as Bearer token."""
        api_key = "phoenix-api-key-123"

        with (
            patch("agent_server.infra.observability.targets.phoenix.settings") as mock_settings,
            patch("agent_server.infra.observability.targets.phoenix.OTLPSpanExporter") as mock_exporter_cls,
        ):
            mock_settings.observability.PHOENIX_COLLECTOR_ENDPOINT = "http://localhost:6006"
            mock_settings.observability.PHOENIX_API_KEY = api_key

            target = PhoenixTarget()
            target.get_exporter()

            assert mock_exporter_cls.call_args.kwargs["headers"] == {"authorization": f"Bearer {api_key}"}
