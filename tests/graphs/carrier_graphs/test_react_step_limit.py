"""Regression tests for the step-limit guard in the ReAct carrier graphs.

The old ``is_last_step`` guard only fires when the model node runs on the last
step, which happens only for even recursion limits. With an odd limit the tools
node takes the last step and the run dies on ``GraphRecursionError``. The guard
now reads ``remaining_steps < 3`` (tools, then the model node, then the answer).

Graphs under test: ``tests/e2e/graphs/react_agent`` and ``stress_tool_agent``.
"""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

CARRIER_GRAPHS_DIR = Path(__file__).resolve().parents[2] / "e2e" / "graphs"


class AlwaysCallsTool:
    """Requests the same tool on every call, so only the step limit can end the loop."""

    def __init__(self, tool_name: str, args: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.args = args
        self.calls = 0

    def bind_tools(self, tools: list[Any]) -> "AlwaysCallsTool":
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls += 1
        return AIMessage(
            content="", tool_calls=[{"name": self.tool_name, "args": self.args, "id": f"call_{self.calls}"}]
        )


@pytest.fixture
def carrier_graphs_on_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.syspath_prepend(str(CARRIER_GRAPHS_DIR))
    yield
    for name in [name for name in sys.modules if name.split(".")[0] in {"react_agent", "stress_tool_agent"}]:
        del sys.modules[name]


def _use_model(monkeypatch: pytest.MonkeyPatch, graph_module: Any, model: AlwaysCallsTool) -> None:
    def load_chat_model(fully_specified_name: str) -> AlwaysCallsTool:
        return model

    monkeypatch.setattr(graph_module, "load_chat_model", load_chat_model)


@pytest.mark.parametrize("recursion_limit", [3, 4, 5, 6, 25])
async def test_react_agent_returns_the_step_limit_message_at_any_recursion_limit(
    monkeypatch: pytest.MonkeyPatch, carrier_graphs_on_path: None, recursion_limit: int
) -> None:
    graph_module = importlib.import_module("react_agent.graph")
    context_module = importlib.import_module("react_agent.context")
    _use_model(monkeypatch, graph_module, AlwaysCallsTool("search", {"query": "template"}))

    result = await graph_module.graph.ainvoke(
        {"messages": [HumanMessage(content="go")]},
        {"recursion_limit": recursion_limit},
        context=context_module.Context(),
    )

    assert result["messages"][-1].content == (
        "Sorry, I could not find an answer to your question in the specified number of steps."
    )


@pytest.mark.parametrize("recursion_limit", [4, 5])
async def test_stress_tool_agent_reports_the_step_limit(
    monkeypatch: pytest.MonkeyPatch, carrier_graphs_on_path: None, recursion_limit: int
) -> None:
    graph_module = importlib.import_module("stress_tool_agent.graph")
    _use_model(monkeypatch, graph_module, AlwaysCallsTool("slow_process", {"step_number": 1}))

    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(graph_module, "asyncio", SimpleNamespace(sleep=no_sleep))

    result = await graph_module.graph.ainvoke(
        {"messages": [HumanMessage(content="Process steps 1 to 10.")]}, {"recursion_limit": recursion_limit}
    )

    assert result["messages"][-1].content == "Processing incomplete — reached step limit."
