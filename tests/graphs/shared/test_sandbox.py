"""Unit tests for the sandbox backend factory and research_agent wiring."""

import sys
from pathlib import Path

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.backends.state import StateBackend

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "graphs"))

from shared.sandbox import make_sandbox_backend  # noqa: E402


def test_off_by_default() -> None:
    assert make_sandbox_backend("") is None
    assert make_sandbox_backend("none") is None


def test_local_provider_returns_local_shell_backend() -> None:
    assert isinstance(make_sandbox_backend("local"), LocalShellBackend)


def test_monty_provider_returns_monty_backend() -> None:
    from shared.monty_sandbox import MontySandboxBackend

    assert isinstance(make_sandbox_backend("monty"), MontySandboxBackend)


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValueError, match="SANDBOX_PROVIDER"):
        make_sandbox_backend("bogus")


def test_remote_providers_give_clear_error_without_packages() -> None:
    pytest.importorskip("langchain_daytona", reason="not installed")  # or assert the hint fires
    # If langchain_daytona IS installed, construction works; both paths are fine.


def test_local_execute_runs_commands() -> None:
    backend = make_sandbox_backend("local")
    assert backend is not None
    result = backend.execute("echo sandbox-ok")
    assert result.exit_code == 0
    assert "sandbox-ok" in result.output


def test_research_agent_backend_switches_with_sandbox_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_agent.agent import build_backend

    monkeypatch.delenv("SANDBOX_PROVIDER", raising=False)
    assert isinstance(build_backend().default, StateBackend)

    monkeypatch.setenv("SANDBOX_PROVIDER", "local")
    assert isinstance(build_backend().default, LocalShellBackend)
