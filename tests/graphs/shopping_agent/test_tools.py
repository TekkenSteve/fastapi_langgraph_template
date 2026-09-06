"""Unit tests for shopping_agent tools (gates + state updates, real fake backend).

Tools are invoked via their ``.coroutine`` directly; ``get_runtime`` is
patched so tools can read per-run context without a graph runner.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from shop.backends import FakeShopBackend
from shopping_agent.state import Context, State
from shopping_agent.tools import make_shop_tools

CONFIG = {"configurable": {"user_id": "test-user"}}


@pytest.fixture(autouse=True)
def _fake_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = SimpleNamespace(context=Context(), store=None)
    monkeypatch.setattr("shopping_agent.tools.get_runtime", lambda *a, **k: runtime)


@pytest.fixture
def harness() -> tuple[FakeShopBackend, dict[str, Any]]:
    backend = FakeShopBackend()
    return backend, {t.name: t for t in make_shop_tools(backend)}


def _state(seen: list[str] | None = None) -> State:
    return State(messages=[], seen_product_ids=seen or [])


async def test_search_products_updates_seen_ids(harness) -> None:
    _, tools = harness
    result = await tools["search_products"].coroutine(query="coffee", tool_call_id="c1")
    assert "coffee-maker-01" in result.update["seen_product_ids"]
    assert "coffee-maker-01" in result.update["messages"][0].content


async def test_add_to_cart_rejected_without_provenance(harness) -> None:
    backend, tools = harness
    result = await tools["add_to_cart"].coroutine(
        product_id="kettle-01", quantity=1, state=_state(), config=CONFIG, tool_call_id="c1"
    )
    assert "not returned by search_products" in result.update["messages"][0].content
    assert (await backend.get_cart("test-user")).lines == []


async def test_add_to_cart_rejected_over_quantity_cap(harness) -> None:
    backend, tools = harness
    result = await tools["add_to_cart"].coroutine(
        product_id="kettle-01",
        quantity=99,
        state=_state(seen=["kettle-01"]),
        config=CONFIG,
        tool_call_id="c2",
    )
    assert "exceeds the per-line limit" in result.update["messages"][0].content
    assert (await backend.get_cart("test-user")).lines == []


async def test_add_to_cart_succeeds_after_search(harness) -> None:
    backend, tools = harness
    result = await tools["add_to_cart"].coroutine(
        product_id="kettle-01",
        quantity=2,
        state=_state(seen=["kettle-01"]),
        config=CONFIG,
        tool_call_id="c1",
    )
    assert "Added 2 × kettle-01" in result.update["messages"][0].content
    assert (await backend.get_cart("test-user")).lines[0].quantity == 2


async def test_checkout_interrupts_for_approval(harness) -> None:
    backend, tools = harness
    await backend.add_to_cart("test-user", "kettle-01", 1)
    # Outside a graph run, interrupt() fails at runnable-context lookup — which
    # proves the approval interrupt is on the checkout path.
    with pytest.raises(Exception, match="(?i)interrupt|runnable context"):
        await tools["checkout"].coroutine(config=CONFIG, tool_call_id="c1")


async def test_checkout_empty_cart_short_circuits(harness) -> None:
    _, tools = harness
    result = await tools["checkout"].coroutine(config=CONFIG, tool_call_id="c1")
    assert "empty" in result.update["messages"][0].content


async def test_remember_preference_without_store(harness) -> None:
    _, tools = harness
    result = await tools["remember_preference"].coroutine(key="roast", value="light", config=CONFIG, tool_call_id="c1")
    assert "unavailable" in result.update["messages"][0].content


def test_tools_expose_nine_contracts(harness) -> None:
    _, tools = harness
    assert set(tools) == {
        "search_products",
        "get_cart",
        "add_to_cart",
        "checkout",
        "remember_preference",
        "present_products",
        "present_comparison",
        "present_checkout_summary",
        "present_suggestions",
    }


def test_context_defaults() -> None:
    ctx = Context()
    assert ctx.model == "openai/gpt-4o-mini"
    assert ctx.max_quantity_per_line == 5


async def test_add_to_cart_writes_live_cart_snapshot(harness) -> None:
    backend, tools = harness
    result = await tools["add_to_cart"].coroutine(
        product_id="kettle-01",
        quantity=2,
        state=_state(seen=["kettle-01"]),
        config=CONFIG,
        tool_call_id="c1",
    )
    cart = result.update["cart"]
    assert cart["lines"][0]["name"] == "Acme Gooseneck Kettle"
    assert cart["lines"][0]["quantity"] == 2
    assert cart["total"] == 91.0
