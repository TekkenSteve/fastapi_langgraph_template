"""Unit tests for the audit ledger writer and reader."""

from types import SimpleNamespace
from typing import Any

import pytest

import agent_server.repo.audit as audit_mod
from agent_server.repo.audit import record_audit_event


class _Session:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1


class _Maker:
    def __init__(self, session: _Session | Exception) -> None:
        self._session = session

    def __call__(self) -> "_Maker":
        return self

    async def __aenter__(self) -> _Session:
        if isinstance(self._session, Exception):
            raise self._session
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> _Session:
    session = _Session()
    monkeypatch.setattr(audit_mod, "get_session_maker", lambda: _Maker(session))
    return session


async def test_record_writes_the_row(session: _Session) -> None:
    await record_audit_event(
        {
            "user_id": "u1",
            "thread_id": "t1",
            "run_id": "r1",
            "action": "tool_call",
            "resource": "web_search",
            "status": "ok",
            "duration_ms": 12,
            "detail": "args={}",
        }
    )

    assert session.commits == 1
    row = session.added[0]
    assert (row.user_id, row.thread_id, row.run_id) == ("u1", "t1", "r1")
    assert (row.action, row.resource, row.status, row.duration_ms) == ("tool_call", "web_search", "ok", 12)
    assert row.detail == {"message": "args={}"}


async def test_record_clips_long_detail(session: _Session) -> None:
    await record_audit_event({"action": "tool_call", "detail": "x" * 5000})

    assert len(session.added[0].detail["message"]) == 2000


async def test_record_without_an_action_is_dropped(session: _Session) -> None:
    await record_audit_event({"status": "ok"})

    assert session.added == []


async def test_a_storage_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ledger that can fail a run is worse than a ledger with holes."""
    monkeypatch.setattr(audit_mod, "get_session_maker", lambda: _Maker(RuntimeError("db down")))

    await record_audit_event({"action": "tool_call"})  # must not raise


async def test_list_applies_every_filter() -> None:
    captured: dict[str, Any] = {}

    class _Result:
        def scalars(self) -> "_Result":
            return self

        def all(self) -> list[Any]:
            return [SimpleNamespace(entry_id=1)]

    class _Session:
        async def execute(self, stmt: Any) -> _Result:
            captured["sql"] = str(stmt)
            return _Result()

    rows = await audit_mod.list_audit_entries(
        _Session(), user_id="u1", thread_id="t1", run_id="r1", action="tool_call", before=9, limit=5
    )

    assert [row.entry_id for row in rows] == [1]
    sql = captured["sql"]
    assert "audit_log.user_id" in sql and "audit_log.thread_id" in sql
    assert "audit_log.run_id" in sql and "audit_log.action" in sql
    assert "audit_log.entry_id <" in sql
    assert "LIMIT" in sql.upper()
