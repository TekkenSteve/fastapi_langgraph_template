"""Unit tests for the skill router middleware and its selectors."""

import tempfile
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.embeddings import Embeddings
from langchain_core.messages import HumanMessage

from research_agent.agent import SKILLS_DIR
from shared.middleware.skill_router import (
    USER_SKILLS_PATH,
    EmbeddingSkillSelector,
    SkillRouterMiddleware,
    _cosine,
    _latest_user_text,
)


class _KeywordSelector:
    """Deterministic selector for tests: picks skills whose name appears in the query."""

    async def select(self, query: str, skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [s for s in skills if s["name"] in query]


def _middleware(**kwargs: Any) -> SkillRouterMiddleware:
    backend = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)
    return SkillRouterMiddleware(backend=backend, sources=[("/", "Project")], selector=_KeywordSelector(), **kwargs)


async def test_filters_to_relevant_skills() -> None:
    middleware = _middleware()
    state = {"messages": [HumanMessage(content="use web-research please")]}
    update = await middleware.abefore_agent(state, None, {})
    names = {s["name"] for s in update["skills_metadata"]}
    assert names == {"web-research"}


async def test_no_matching_query_keeps_empty_selection() -> None:
    middleware = _middleware()
    state = {"messages": [HumanMessage(content="hello")]}
    update = await middleware.abefore_agent(state, None, {})
    assert update["skills_metadata"] == []


async def test_always_include_survives_filtering() -> None:
    middleware = _middleware(always_include=["source-critic"])
    state = {"messages": [HumanMessage(content="use web-research please")]}
    update = await middleware.abefore_agent(state, None, {})
    names = {s["name"] for s in update["skills_metadata"]}
    assert names == {"web-research", "source-critic"}


async def test_top_k_caps_the_listing() -> None:
    middleware = _middleware(top_k=1, always_include=[])
    state = {"messages": [HumanMessage(content="web-research and source-critic")]}
    update = await middleware.abefore_agent(state, None, {})
    assert len(update["skills_metadata"]) == 1


async def test_skips_when_state_already_has_skills() -> None:
    middleware = _middleware()
    state = {"messages": [], "skills_metadata": []}
    assert await middleware.abefore_agent(state, None, {}) is None


def test_latest_user_text_reads_last_human_message() -> None:
    state = {
        "messages": [
            HumanMessage(content="first"),
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "latest"},
        ]
    }
    assert _latest_user_text(state) == "latest"


def test_cosine_basics() -> None:
    assert _cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert _cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert _cosine([0, 0], [1, 1]) == 0.0


class _FakeEmbeddings(Embeddings):
    """One-hot over a fixed test vocabulary — deterministic and collision-free."""

    _VOCAB = ["research", "topic", "search", "web-research", "source", "critic", "credibility", "evaluate"]

    def _vec(self, text: str) -> list[float]:
        lowered = text.lower()
        return [1.0 if word in lowered else 0.0 for word in self._VOCAB]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


async def test_embedding_selector_ranks_by_similarity() -> None:
    skills = [
        {"name": "web-research", "description": "research topics with search"},
        {"name": "source-critic", "description": "evaluate source credibility"},
    ]
    selector = EmbeddingSkillSelector(_FakeEmbeddings(), top_k=1)
    selected = await selector.select("research a topic", skills)
    assert selected[0]["name"] == "web-research"


# --- user-tier materialization (skill hub) -----------------------------------


def _user_skill_md(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"


def _hub_middleware(tmp: str, loader):
    """Middleware whose default backend is a writable tmp dir — StateBackend
    requires a live graph context, which a unit test does not have."""
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=Path(tmp), virtual_mode=True),
        routes={"/skills/": FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)},
    )
    return SkillRouterMiddleware(
        backend=backend,
        sources=[("/skills/", "Project")],
        selector=_KeywordSelector(),
        user_skills_loader=loader,
    )


async def test_user_skills_loader_appends_user_source() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        middleware = _hub_middleware(tmp, loader=None)
        assert USER_SKILLS_PATH not in middleware.sources
        middleware = _hub_middleware(tmp, loader=lambda uid: [])
        assert middleware.sources[-1] == USER_SKILLS_PATH
        assert middleware.source_labels[-1] == "User"


async def test_materializes_user_skills_into_default_backend() -> None:
    async def loader(user_id: str) -> list[dict[str, Any]]:
        return [
            {
                "name": "coffee-brewing",
                "files": {
                    "SKILL.md": _user_skill_md("coffee-brewing", "brew coffee"),
                    "scripts/ratio.py": "def ratio(d):\n    return d * 16\n",
                },
            }
        ]

    with tempfile.TemporaryDirectory() as tmp:
        middleware = _hub_middleware(tmp, loader)
        state = {"messages": [HumanMessage(content="coffee-brewing please")]}
        update = await middleware.abefore_agent(state, None, {"configurable": {"user_id": "u1"}})

        names = {s["name"] for s in update["skills_metadata"]}
        assert "coffee-brewing" in names
        assert (Path(tmp) / "user-skills" / "coffee-brewing" / "SKILL.md").exists()
        assert (Path(tmp) / "user-skills" / "coffee-brewing" / "scripts" / "ratio.py").exists()


async def test_user_skill_overrides_same_named_builtin() -> None:
    async def loader(user_id: str) -> list[dict[str, Any]]:
        return [{"name": "web-research", "files": {"SKILL.md": _user_skill_md("web-research", "USER version")}}]

    with tempfile.TemporaryDirectory() as tmp:
        middleware = _hub_middleware(tmp, loader)
        state = {"messages": [HumanMessage(content="web-research")]}
        update = await middleware.abefore_agent(state, None, {"configurable": {"user_id": "u1"}})

        by_name = {s["name"]: s for s in update["skills_metadata"]}
        assert by_name["web-research"]["description"] == "USER version"
        assert by_name["web-research"]["path"].startswith(USER_SKILLS_PATH)


async def test_loader_failure_degrades_to_builtin_only() -> None:
    async def loader(user_id: str) -> list[dict[str, Any]]:
        raise ConnectionError("db down")

    with tempfile.TemporaryDirectory() as tmp:
        middleware = _hub_middleware(tmp, loader)
        state = {"messages": [HumanMessage(content="web-research")]}
        update = await middleware.abefore_agent(state, None, {"configurable": {"user_id": "u1"}})
        assert {s["name"] for s in update["skills_metadata"]} == {"web-research"}


async def test_no_user_id_skips_loader() -> None:
    called = False

    async def loader(user_id: str) -> list[dict[str, Any]]:
        nonlocal called
        called = True
        return []

    with tempfile.TemporaryDirectory() as tmp:
        middleware = _hub_middleware(tmp, loader)
        state = {"messages": [HumanMessage(content="web-research")]}
        await middleware.abefore_agent(state, None, {})
        assert not called
