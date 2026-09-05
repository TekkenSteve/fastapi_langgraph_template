"""Unit tests for merchant analytics budgets and apply-time guardrail recheck."""

import json
from types import SimpleNamespace

import pytest

from merchant_agent.state import Context, State
from merchant_agent.tools import make_merchant_tools
from shop.backends import FakeShopBackend

CONFIG = {"configurable": {"user_id": "staff-1"}}


def _harness(**ctx_kwargs):
    backend = FakeShopBackend()
    tools = {t.name: t for t in make_merchant_tools(backend)}
    return backend, tools, SimpleNamespace(context=Context(**ctx_kwargs), store=None)


def _runtime_proxy(monkeypatch: pytest.MonkeyPatch, runtime) -> None:
    monkeypatch.setattr("merchant_agent.tools.get_runtime", lambda *a, **k: runtime)


async def test_analytics_rejects_non_select(monkeypatch: pytest.MonkeyPatch) -> None:
    _, tools, runtime = _harness()
    _runtime_proxy(monkeypatch, runtime)
    result = await tools["analytics_query"].coroutine(
        query="DELETE FROM orders", state=State(messages=[]), tool_call_id="c1"
    )
    content = json.loads(result.update["messages"][0].content)
    assert content["status"] == "blocked"
    assert content["gate"] == "read_only"


async def test_analytics_rejects_multi_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    _, tools, runtime = _harness()
    _runtime_proxy(monkeypatch, runtime)
    result = await tools["analytics_query"].coroutine(
        query="SELECT 1; SELECT 2", state=State(messages=[]), tool_call_id="c1"
    )
    assert json.loads(result.update["messages"][0].content)["gate"] == "read_only"


async def test_analytics_per_turn_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    _, tools, runtime = _harness(analytics_max_calls_per_turn=1)
    _runtime_proxy(monkeypatch, runtime)
    state = State(messages=[])
    first = await tools["analytics_query"].coroutine(query="SELECT 1", state=state, tool_call_id="c1")
    assert json.loads(first.update["messages"][0].content)["status"] == "ok"
    state.analytics_calls = first.update["analytics_calls"]

    second = await tools["analytics_query"].coroutine(query="SELECT 1", state=state, tool_call_id="c2")
    content = json.loads(second.update["messages"][0].content)
    assert content["status"] == "blocked"
    assert content["gate"] == "budget"


async def test_analytics_disabled_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _, tools, runtime = _harness(enable_analytics=False)
    _runtime_proxy(monkeypatch, runtime)
    result = await tools["analytics_query"].coroutine(query="SELECT 1", state=State(messages=[]), tool_call_id="c1")
    assert json.loads(result.update["messages"][0].content)["gate"] == "config"


async def test_apply_rechecks_guardrail_with_current_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A change staged under a lax cap must be refused when the cap has since
    been tightened — apply-time revalidation with the config in force now."""
    backend, tools, runtime = _harness(max_price_move_pct=1.0)
    change = await backend.stage_price_change("staff-1", "kettle-01", 42.0)

    # Cap tightened to 1% after staging: 45.50 → 42.0 is a 7.7% move.
    _runtime_proxy(monkeypatch, runtime)
    state = State(messages=[], seen_listing_ids=["kettle-01"], staged_change_ids=[change.change_id])
    result = await tools["apply_change"].coroutine(
        change_id=change.change_id, state=state, config=CONFIG, tool_call_id="c1"
    )

    content = json.loads(result.update["messages"][0].content)
    assert content["status"] == "blocked"
    assert content["gate"] == "guardrail"
    assert (await backend.get_listing("kettle-01")).price == 45.50  # unchanged


async def test_apply_blocks_when_no_longer_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeShopBackend()
    change = await backend.stage_price_change("staff-1", "kettle-01", 42.0)
    await backend.discard_change("staff-1", change.change_id)

    _, tools, runtime = _harness()
    _runtime_proxy(monkeypatch, runtime)
    state = State(messages=[], staged_change_ids=[change.change_id])
    result = await tools["apply_change"].coroutine(
        change_id=change.change_id, state=state, config=CONFIG, tool_call_id="c1"
    )
    assert "no longer pending" in result.update["messages"][0].content
