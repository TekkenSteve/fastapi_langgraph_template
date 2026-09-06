"""Unit tests for generative-UI presentation tools."""

import json
from types import SimpleNamespace

import pytest

from shop.backends import FakeShopBackend
from shopping_agent.state import Context, State
from shopping_agent.tools import make_shop_tools


@pytest.fixture(autouse=True)
def _fake_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = SimpleNamespace(context=Context(), store=None)
    monkeypatch.setattr("shopping_agent.tools.get_runtime", lambda *a, **k: runtime)


@pytest.fixture(autouse=True)
def _no_stream_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict] = []
    monkeypatch.setattr("shared.presentation.get_stream_writer", lambda: events.append)
    return events


def _state(seen: list[str]) -> State:
    return State(messages=[], seen_product_ids=seen)


async def test_present_products_enriches_from_backend(harness_tools=None) -> None:
    backend = FakeShopBackend()
    tools = {t.name: t for t in make_shop_tools(backend)}
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


async def test_present_products_drops_unprovenanced_ids() -> None:
    backend = FakeShopBackend()
    tools = {t.name: t for t in make_shop_tools(backend)}
    result = await tools["present_products"].coroutine(
        product_ids=["kettle-01", "invented-99"], reasons=[], state=_state(["kettle-01"]), config={}, tool_call_id="c1"
    )
    payload = result.update["presentations"][0]["payload"]
    assert [p["id"] for p in payload["products"]] == ["kettle-01"]
    note = json.loads(result.update["messages"][0].content)["result"]
    assert "invented-99 was dropped" in note


async def test_present_products_refused_when_nothing_provenanced() -> None:
    backend = FakeShopBackend()
    tools = {t.name: t for t in make_shop_tools(backend)}
    result = await tools["present_products"].coroutine(
        product_ids=["invented-99"], reasons=[], state=_state([]), config={}, tool_call_id="c1"
    )
    content = json.loads(result.update["messages"][0].content)
    assert content["status"] == "blocked"
    assert content["gate"] == "provenance"
    assert "presentations" not in result.update


async def test_present_suggestions_sanitizes_chips() -> None:
    backend = FakeShopBackend()
    tools = {t.name: t for t in make_shop_tools(backend)}
    result = await tools["present_suggestions"].coroutine(
        suggestions=["Show  kettles", "​", "Compare espresso machines", "Deals"],
        state=_state([]),
        config={},
        tool_call_id="c1",
    )
    chips = result.update["presentations"][0]["payload"]["suggestions"]
    assert chips == ["Show kettles", "Compare espresso machines", "Deals"]


async def test_present_suggestions_refuses_all_empty() -> None:
    backend = FakeShopBackend()
    tools = {t.name: t for t in make_shop_tools(backend)}
    result = await tools["present_suggestions"].coroutine(
        suggestions=["​"], state=_state([]), config={}, tool_call_id="c1"
    )
    assert json.loads(result.update["messages"][0].content)["status"] == "error"


async def test_present_comparison_joins_two_products() -> None:
    backend = FakeShopBackend()
    tools = {t.name: t for t in make_shop_tools(backend)}
    result = await tools["present_comparison"].coroutine(
        product_ids=["kettle-01", "scale-01"],
        state=_state(["kettle-01", "scale-01"]),
        config={},
        tool_call_id="c1",
    )
    block = result.update["presentations"][0]
    assert block["component"] == "ComparisonGrid"
    assert [p["name"] for p in block["payload"]["products"]] == ["Acme Gooseneck Kettle", "Acme Coffee Scale"]


async def test_present_comparison_refused_with_single_product() -> None:
    backend = FakeShopBackend()
    tools = {t.name: t for t in make_shop_tools(backend)}
    result = await tools["present_comparison"].coroutine(
        product_ids=["kettle-01", "invented-99"],
        state=_state(["kettle-01"]),
        config={},
        tool_call_id="c1",
    )
    content = json.loads(result.update["messages"][0].content)
    assert content["status"] == "blocked"
    assert content["gate"] == "provenance"


async def test_checkout_summary_joins_cart_from_server() -> None:
    backend = FakeShopBackend()
    await backend.add_to_cart("demo-user", "kettle-01", 2)
    tools = {t.name: t for t in make_shop_tools(backend)}
    result = await tools["present_checkout_summary"].coroutine(state=_state([]), config={}, tool_call_id="c1")
    payload = result.update["presentations"][0]["payload"]
    assert payload["lines"][0]["name"] == "Acme Gooseneck Kettle"
    assert payload["total"] == 91.0


async def test_checkout_summary_blocked_when_cart_empty() -> None:
    backend = FakeShopBackend()
    tools = {t.name: t for t in make_shop_tools(backend)}
    result = await tools["present_checkout_summary"].coroutine(state=_state([]), config={}, tool_call_id="c1")
    content = json.loads(result.update["messages"][0].content)
    assert content["status"] == "blocked"
    assert content["gate"] == "empty_cart"
