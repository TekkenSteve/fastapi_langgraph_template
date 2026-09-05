"""Skill router middleware: per-request top-k skill selection.

SkillsMiddleware lists every skill statically; beyond ~20 skills the listing
itself starts to crowd the context. This subclass keeps progressive disclosure
but selects only the most relevant skills for the current request before the
model ever sees the list.

Selection is pluggable via SkillSelector — mirror of langchain's
LLMToolSelectorMiddleware idea, applied to skills. Two selectors ship here:
an LLM selector (cheap model picks from the catalog) and an embedding
selector (cosine similarity over name+description; the server's pgvector
store is the production-grade version of this).

NOTE: uses deepagents' private `_alist_skills` loader — revisit on upgrades.
"""

from collections.abc import Sequence
from typing import Any, Protocol

import structlog
from deepagents.middleware.skills import (
    SkillMetadata,
    SkillsMiddleware,
    SkillsStateUpdate,
    _alist_skills,
)
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from shared.models import load_chat_model

logger = structlog.getLogger(__name__)

_SELECT_PROMPT = """Select the skills relevant to the user's request.

Available skills:
{catalog}

Reply with the skill names only, one per line, most relevant first. Select at
most {top_k}. If none apply, reply with NONE."""


class SkillSelector(Protocol):
    """Picks the relevant subset of a skill catalog for one request."""

    async def select(self, query: str, skills: list[SkillMetadata]) -> list[SkillMetadata]: ...


class LLMSkillSelector:
    """A cheap model picks skills from the catalog (name + description)."""

    def __init__(self, model: str, *, top_k: int) -> None:
        self._model_name = model
        self._top_k = top_k

    async def select(self, query: str, skills: list[SkillMetadata]) -> list[SkillMetadata]:
        catalog = "\n".join(f"- {s['name']}: {s['description']}" for s in skills)
        model: BaseChatModel = load_chat_model(self._model_name.replace(":", "/", 1))
        response = await model.ainvoke(
            [SystemMessage(_SELECT_PROMPT.format(catalog=catalog, top_k=self._top_k)), HumanMessage(query)]
        )
        text = response.content if isinstance(response.content, str) else ""
        picked = {line.strip().lstrip("- ") for line in text.splitlines() if line.strip()}
        selected = [s for s in skills if s["name"] in picked]
        if not selected:
            logger.info("skill_router_llm_fallback_to_all", query_len=len(query))
            return skills[: self._top_k]
        return selected[: self._top_k]


class EmbeddingSkillSelector:
    """Cosine similarity between the query and each skill's name+description."""

    def __init__(self, embeddings: Embeddings, *, top_k: int) -> None:
        self._embeddings = embeddings
        self._top_k = top_k

    async def select(self, query: str, skills: list[SkillMetadata]) -> list[SkillMetadata]:
        query_vec = await self._embeddings.aembed_query(query)
        docs = [f"{s['name']}: {s['description']}" for s in skills]
        doc_vecs = await self._embeddings.aembed_documents(docs)
        scored = sorted(
            zip(skills, doc_vecs, strict=True),
            key=lambda pair: _cosine(query_vec, pair[1]),
            reverse=True,
        )
        return [skill for skill, _ in scored[: self._top_k]]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SkillRouterMiddleware(SkillsMiddleware):
    """SkillsMiddleware that filters the catalog to top-k per request.

    Same constructor plus:
        selector: SkillSelector deciding relevance per request
        top_k / always_include: selection bounds (always_include survives every filter)
    """

    def __init__(
        self,
        *,
        selector: SkillSelector,
        top_k: int = 5,
        always_include: Sequence[str] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._selector = selector
        self._top_k = top_k
        self._always_include = tuple(always_include)

    async def _load_all_skills(self) -> list[SkillMetadata]:
        skills: list[SkillMetadata] = []
        for source in self.sources:
            skills.extend(await _alist_skills(self._backend, source))
        # Later sources override earlier ones by name (deepagents semantics).
        by_name: dict[str, SkillMetadata] = {}
        for skill in skills:
            by_name[skill["name"]] = skill
        return list(by_name.values())

    async def abefore_agent(
        self, state: dict[str, Any], runtime: Runtime, config: RunnableConfig
    ) -> SkillsStateUpdate | None:
        if "skills_metadata" in state:
            return None

        all_skills = await self._load_all_skills()
        if not all_skills:
            return SkillsStateUpdate(skills_metadata=[])

        query = _latest_user_text(state)
        selected = await self._selector.select(query, all_skills) if query else all_skills[: self._top_k]

        # always_include entries survive every filter, without duplicates
        selected_names = {s["name"] for s in selected}
        pinned = [s for s in all_skills if s["name"] in self._always_include and s["name"] not in selected_names]
        skills = [*pinned, *selected][: self._top_k]

        logger.info("skill_router_selected", count=len(skills), names=[s["name"] for s in skills])
        return SkillsStateUpdate(skills_metadata=skills)


def _latest_user_text(state: dict[str, Any]) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage) or (isinstance(message, dict) and message.get("role") == "user"):
            content = message.content if isinstance(message, HumanMessage) else message.get("content", "")
            return content if isinstance(content, str) else ""
    return ""
