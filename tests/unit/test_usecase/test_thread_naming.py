"""Tests for thread auto-naming (usecase/thread_naming.py)."""

from unittest.mock import MagicMock

import agent_server.usecase.thread_naming as naming
from agent_server.usecase.thread_naming import _extract_first_user_message, maybe_name_thread


def test_extract_first_user_message_from_messages_input() -> None:
    payload = {"messages": [{"role": "assistant", "content": "hi"}, {"role": "user", "content": "  buy a kettle  "}]}
    assert _extract_first_user_message(payload) == "buy a kettle"


def test_extract_first_user_message_none_for_non_dict_input() -> None:
    assert _extract_first_user_message("hello") is None
    assert _extract_first_user_message({"messages": "not-a-list"}) is None
    assert _extract_first_user_message({"messages": [{"role": "assistant", "content": "hi"}]}) is None


class _FakeSession:
    def __init__(self, rowcount: int) -> None:
        self._rowcount = rowcount
        self.committed = False

    async def execute(self, stmt):
        result = MagicMock()
        result.rowcount = self._rowcount
        return result

    async def commit(self) -> None:
        self.committed = True


class _FakeMaker:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return None


async def test_claim_wins_and_schedules_background_title(monkeypatch) -> None:
    session = _FakeSession(rowcount=1)
    monkeypatch.setattr(naming, "get_session_maker", lambda: _FakeMaker(session))
    ran = []

    async def _fake_generate(thread_id, message):
        ran.append((thread_id, message))

    monkeypatch.setattr(naming, "_generate_and_set_title", _fake_generate)
    scheduled = []

    def _capture(coro):
        scheduled.append(coro)
        task = MagicMock()
        task.add_done_callback = lambda cb: None
        return task

    monkeypatch.setattr(naming.asyncio, "create_task", _capture)

    await maybe_name_thread("t1", {"messages": [{"role": "user", "content": "recommend a coffee maker"}]})

    assert session.committed
    assert len(scheduled) == 1  # background generation scheduled
    await scheduled[0]  # run it
    assert ran == [("t1", "recommend a coffee maker")]


async def test_claim_lost_means_no_background_work(monkeypatch) -> None:
    session = _FakeSession(rowcount=0)
    monkeypatch.setattr(naming, "get_session_maker", lambda: _FakeMaker(session))
    scheduled = []

    def _capture(coro):
        scheduled.append(coro)
        task = MagicMock()
        task.add_done_callback = lambda cb: None
        return task

    monkeypatch.setattr(naming.asyncio, "create_task", _capture)

    await maybe_name_thread("t1", {"messages": [{"role": "user", "content": "hi"}]})

    assert session.committed
    assert scheduled == []  # another run already claimed naming


async def test_no_user_message_is_a_noop(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(naming, "get_session_maker", lambda: called.append(1) or None)
    await maybe_name_thread("t1", {"messages": []})
    assert called == []


async def test_disabled_setting_short_circuits(monkeypatch) -> None:
    monkeypatch.setattr(naming.settings.app, "THREAD_NAMING_ENABLED", False)
    called = []
    monkeypatch.setattr(naming, "get_session_maker", lambda: called.append(1) or None)
    await maybe_name_thread("t1", {"messages": [{"role": "user", "content": "hi"}]})
    assert called == []
