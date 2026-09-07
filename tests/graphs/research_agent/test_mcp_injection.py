"""research_agent's MCP contract: one-line composition at the entry."""

from research_agent.graph import graph


def test_entry_is_a_zero_arg_async_factory() -> None:
    """The wrapped entry is what the framework expects at load time."""
    import inspect

    assert inspect.iscoroutinefunction(graph)
    assert len(inspect.signature(graph).parameters) == 0


def test_factory_builds_with_injected_tools() -> None:
    import asyncio

    built = asyncio.run(graph())
    assert built is not None
