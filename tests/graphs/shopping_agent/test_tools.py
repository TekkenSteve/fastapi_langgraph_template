"""Unit tests for shopping_agent tools (gates + state updates, real fake backend).

Tools are invoked via their ``.coroutine`` directly; ``get_runtime`` is
patched so tools can read per-run context without a graph runner.
"""

import json
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


class TestPresentationTools:
    """Generative-UI tools: facts are joined server-side, never model-authored."""

    @pytest.fixture(autouse=True)
    def _no_stream_writer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shared.presentation.get_stream_writer", lambda: lambda _event: None)

    async def test_present_products_enriches_from_backend(self, harness) -> None:
        _, tools = harness
        result = await tools["present_products"].coroutine(
            product_ids=["kettle-01"],
            reasons=["precise pouring"],
            state=_state(["kettle-01"]),
            config={},
            tool_call_id="c1",
        )
        block = result.update["presentations"][0]
        assert block["component"] == "ProductCarousel"
        product = block["payload"]["products"][0]
        # facts are joined server-side, not model-authored
        assert product["name"] == "Acme Gooseneck Kettle"
        assert product["price"] == 45.50
        assert product["reason"] == "precise pouring"

    async def test_present_products_drops_unprovenanced_ids(self, harness) -> None:
        _, tools = harness
        result = await tools["present_products"].coroutine(
            product_ids=["kettle-01", "invented-99"],
            reasons=[],
            state=_state(["kettle-01"]),
            config={},
            tool_call_id="c1",
        )
        payload = result.update["presentations"][0]["payload"]
        assert [p["id"] for p in payload["products"]] == ["kettle-01"]
        note = json.loads(result.update["messages"][0].content)["result"]
        assert "invented-99 was dropped" in note

    async def test_present_products_refused_when_nothing_provenanced(self, harness) -> None:
        _, tools = harness
        result = await tools["present_products"].coroutine(
            product_ids=["invented-99"], reasons=[], state=_state([]), config={}, tool_call_id="c1"
        )
        content = json.loads(result.update["messages"][0].content)
        assert content["status"] == "blocked"
        assert content["gate"] == "provenance"
        assert "presentations" not in result.update

    async def test_present_suggestions_sanitizes_chips(self, harness) -> None:
        _, tools = harness
        result = await tools["present_suggestions"].coroutine(
            suggestions=["Show  kettles", "​", "Compare espresso machines", "Deals"],
            state=_state([]),
            config={},
            tool_call_id="c1",
        )
        chips = result.update["presentations"][0]["payload"]["suggestions"]
        assert chips == ["Show kettles", "Compare espresso machines", "Deals"]

    async def test_present_suggestions_refuses_all_empty(self, harness) -> None:
        _, tools = harness
        result = await tools["present_suggestions"].coroutine(
            suggestions=["​"], state=_state([]), config={}, tool_call_id="c1"
        )
        assert json.loads(result.update["messages"][0].content)["status"] == "error"

    async def test_present_comparison_joins_two_products(self, harness) -> None:
        _, tools = harness
        result = await tools["present_comparison"].coroutine(
            product_ids=["kettle-01", "scale-01"],
            state=_state(["kettle-01", "scale-01"]),
            config={},
            tool_call_id="c1",
        )
        block = result.update["presentations"][0]
        assert block["component"] == "ComparisonGrid"
        assert [p["name"] for p in block["payload"]["products"]] == ["Acme Gooseneck Kettle", "Acme Coffee Scale"]

    async def test_present_comparison_refused_with_single_product(self, harness) -> None:
        _, tools = harness
        result = await tools["present_comparison"].coroutine(
            product_ids=["kettle-01", "invented-99"],
            state=_state(["kettle-01"]),
            config={},
            tool_call_id="c1",
        )
        content = json.loads(result.update["messages"][0].content)
        assert content["status"] == "blocked"
        assert content["gate"] == "provenance"

    async def test_checkout_summary_joins_cart_from_server(self, harness) -> None:
        backend, tools = harness
        await backend.add_to_cart("demo-user", "kettle-01", 2)
        result = await tools["present_checkout_summary"].coroutine(state=_state([]), config={}, tool_call_id="c1")
        payload = result.update["presentations"][0]["payload"]
        assert payload["lines"][0]["name"] == "Acme Gooseneck Kettle"
        assert payload["total"] == 91.0

    async def test_checkout_summary_blocked_when_cart_empty(self, harness) -> None:
        _, tools = harness
        result = await tools["present_checkout_summary"].coroutine(state=_state([]), config={}, tool_call_id="c1")
        content = json.loads(result.update["messages"][0].content)
        assert content["status"] == "blocked"
        assert content["gate"] == "empty_cart"
