"""Unit tests for merchant tools: staged-writes lifecycle with gates."""

from types import SimpleNamespace

import pytest

from merchant_agent.state import Context, State
from merchant_agent.tools import make_merchant_tools
from shop.backends import FakeShopBackend

CONFIG = {"configurable": {"user_id": "staff-1"}}


@pytest.fixture(autouse=True)
def _fake_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = SimpleNamespace(context=Context(), store=None)
    monkeypatch.setattr("merchant_agent.tools.get_runtime", lambda *a, **k: runtime)


@pytest.fixture
def harness() -> tuple[FakeShopBackend, dict]:
    backend = FakeShopBackend()
    return backend, {t.name: t for t in make_merchant_tools(backend)}


def _state(seen: list[str] | None = None, staged: list[str] | None = None) -> State:
    return State(messages=[], seen_listing_ids=seen or [], staged_change_ids=staged or [])


async def test_get_listing_marks_provenance(harness) -> None:
    _, tools = harness
    result = await tools["get_listing"].coroutine(product_id="kettle-01", tool_call_id="c1")
    assert "kettle-01" in result.update["seen_listing_ids"]
    assert "$45.50" in result.update["messages"][0].content


async def test_stage_rejected_without_reading_listing(harness) -> None:
    backend, tools = harness
    result = await tools["stage_price_change"].coroutine(
        product_id="kettle-01", new_price=39.99, state=_state(), config=CONFIG, tool_call_id="c1"
    )
    assert "Cannot stage" in result.update["messages"][0].content
    assert await backend.get_pending_changes("staff-1") == []


async def test_stage_rejected_over_price_move_cap(harness) -> None:
    _, tools = harness
    result = await tools["stage_price_change"].coroutine(
        product_id="kettle-01",
        new_price=9.99,  # -78%: way over the 20% guardrail
        state=_state(seen=["kettle-01"]),
        config=CONFIG,
        tool_call_id="c1",
    )
    assert "exceeds" in result.update["messages"][0].content


async def test_stage_then_apply_lifecycle(harness) -> None:
    backend, tools = harness
    staged = await tools["stage_price_change"].coroutine(
        product_id="kettle-01", new_price=42.0, state=_state(seen=["kettle-01"]), config=CONFIG, tool_call_id="c1"
    )
    change_id = staged.update["staged_change_ids"][0]
    assert "Staged for review" in staged.update["messages"][0].content

    pending = await backend.get_pending_changes("staff-1")
    assert pending[0].status == "pending"

    # apply_change must interrupt for approval — outside a runnable context this
    # fails at the context lookup, proving the approval gate is on the path.
    with pytest.raises(Exception, match="(?i)interrupt|runnable context"):
        await tools["apply_change"].coroutine(
            change_id=change_id, state=_state(staged=[change_id]), config=CONFIG, tool_call_id="c2"
        )

    await backend.apply_change("staff-1", change_id)
    assert (await backend.get_listing("kettle-01")).price == 42.0


async def test_apply_rejects_foreign_change_id(harness) -> None:
    _, tools = harness
    result = await tools["apply_change"].coroutine(
        change_id="change-9999", state=_state(), config=CONFIG, tool_call_id="c1"
    )
    assert "Cannot apply" in result.update["messages"][0].content


async def test_discard_removes_from_pending(harness) -> None:
    backend, tools = harness
    staged = await tools["stage_price_change"].coroutine(
        product_id="kettle-01", new_price=42.0, state=_state(seen=["kettle-01"]), config=CONFIG, tool_call_id="c1"
    )
    change_id = staged.update["staged_change_ids"][0]
    result = await tools["discard_change"].coroutine(
        change_id=change_id, state=_state(staged=[change_id]), config=CONFIG, tool_call_id="c2"
    )
    assert "Discarded" in result.update["messages"][0].content
    assert await backend.get_pending_changes("staff-1") == []
