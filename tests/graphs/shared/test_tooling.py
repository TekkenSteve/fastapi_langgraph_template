"""Unit tests for the structured tool result envelope."""

import json

from shared.tooling import tool_blocked, tool_error, tool_ok


def _content(cmd) -> dict:
    return json.loads(cmd.update["messages"][0].content)


def test_tool_ok_envelope() -> None:
    cmd = tool_ok({"answer": 42}, "c1")
    assert _content(cmd) == {"status": "ok", "result": {"answer": 42}}


def test_tool_ok_carries_state_update() -> None:
    cmd = tool_ok("done", "c1", state_update={"seen": ["x"]})
    assert cmd.update["seen"] == ["x"]


def test_tool_blocked_envelope_names_the_gate() -> None:
    cmd = tool_blocked("provenance", "id not seen", "c1")
    assert _content(cmd) == {"status": "blocked", "gate": "provenance", "reason": "id not seen"}


def test_tool_error_envelope() -> None:
    cmd = tool_error("backend down", "c1")
    assert _content(cmd) == {"status": "error", "reason": "backend down"}
