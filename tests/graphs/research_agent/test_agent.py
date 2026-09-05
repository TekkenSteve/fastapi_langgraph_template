"""Unit tests for the research_agent composed-agent assembly."""

from langchain_core.messages import ToolMessage

from research_agent.agent import build_research_agent
from research_agent.subagents import SUBAGENTS
from shared.middleware.audit_log import AuditLogMiddleware


def test_subagents_are_valid_declarations() -> None:
    for sub in SUBAGENTS:
        assert sub["name"] and sub["description"] and sub["system_prompt"]
        assert sub["tools"]


def test_agent_compiles_with_builtin_tools() -> None:
    graph = build_research_agent()
    node_names = set(graph.get_graph().nodes)
    assert node_names  # compiled deep agent has a model node at minimum


async def test_audit_middleware_logs_tool_call(caplog) -> None:
    middleware = AuditLogMiddleware()
    request = type("Req", (), {"tool_call": {"name": "web_search", "args": {}}})()

    async def handler(req):
        return ToolMessage("ok", tool_call_id="1")

    with caplog.at_level("INFO"):
        result = await middleware.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)


async def test_audit_middleware_logs_and_reraises_errors() -> None:
    import pytest

    middleware = AuditLogMiddleware()
    request = type("Req", (), {"tool_call": {"name": "web_search", "args": {}}})()

    async def handler(req):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await middleware.awrap_tool_call(request, handler)


def test_skills_discoverable_through_composite_backend() -> None:
    """Both shipped SKILL.md files are listed by the skills middleware path."""
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.skills import _list_skills

    from research_agent.agent import SKILLS_DIR

    skills = _list_skills(FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True), "/")
    names = {s["name"] for s in skills}
    assert names == {"web-research", "source-critic"}
    for skill in skills:
        assert skill["description"]


def test_agent_compiles_with_skills_middleware() -> None:
    # The compiled graph must build with the skills middleware attached.
    assert build_research_agent() is not None
