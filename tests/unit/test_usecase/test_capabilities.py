"""Unit tests for the capability report (readiness, not flags)."""

import pytest

from agent_server.config.settings import settings
from agent_server.usecase import capabilities as caps


def test_sandbox_off_is_reported_as_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.sandbox, "SANDBOX_PROVIDER", "")

    report = caps.sandbox_capability()

    assert report["ready"] is False
    assert "inert" in report["detail"]


def test_a_selected_sandbox_without_its_package_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """The half-ready state this report exists for: flag set, package absent."""
    monkeypatch.setattr(settings.sandbox, "SANDBOX_PROVIDER", "daytona")
    monkeypatch.setattr(caps, "_installed", lambda _module: False)

    report = caps.sandbox_capability()

    assert report["ready"] is False
    assert "langchain_daytona is not installed" in report["detail"]


def test_an_installed_sandbox_tier_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.sandbox, "SANDBOX_PROVIDER", "monty")
    monkeypatch.setattr(caps, "_installed", lambda _module: True)

    assert caps.sandbox_capability() == {"ready": True, "detail": "monty"}


def test_local_sandbox_is_ready_but_named_as_unisolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.sandbox, "SANDBOX_PROVIDER", "local")

    report = caps.sandbox_capability()

    assert report["ready"] is True
    assert "no isolation" in report["detail"]


def test_an_unknown_provider_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.sandbox, "SANDBOX_PROVIDER", "bogus")

    assert caps.sandbox_capability()["ready"] is False


def test_tool_authorization_without_a_backend_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed by design: the local engine denies every tool resource."""
    monkeypatch.setattr(settings.mcp, "MCP_TOOL_AUTHZ_ENABLED", True)
    monkeypatch.setattr(settings.policy, "OPA_URL", "")

    report = caps.tool_authorization_capability()

    assert report["ready"] is False
    assert "deny" in report["detail"]


def test_tool_authorization_with_opa_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.mcp, "MCP_TOOL_AUTHZ_ENABLED", True)
    monkeypatch.setattr(settings.policy, "OPA_URL", "http://opa:8181")

    assert caps.tool_authorization_capability() == {"ready": True, "detail": "opa"}


def test_authentication_reports_noop_and_missing_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.app, "AUTH_TYPE", "noop")
    assert caps.authentication_capability()["ready"] is False

    monkeypatch.setattr(settings.app, "AUTH_TYPE", "jwt")
    monkeypatch.setattr(caps, "load_auth_config", lambda: None)
    report = caps.authentication_capability()
    assert report["ready"] is False
    assert "no auth.path handler" in report["detail"]

    monkeypatch.setattr(caps, "load_auth_config", lambda: {"path": "auth.py"})
    assert caps.authentication_capability() == {"ready": True, "detail": "jwt"}


def test_the_report_covers_every_documented_capability() -> None:
    report = caps.build_capability_report()

    assert set(report) == {
        "authentication",
        "sandbox",
        "tool_authorization",
        "mcp_apps",
        "audit_ledger",
        "rate_limit",
        "multi_instance",
    }
    assert all({"ready", "detail"} == set(entry) for entry in report.values())
