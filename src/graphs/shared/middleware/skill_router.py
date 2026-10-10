"""Skill router middleware: top-k skill selection, seeded once per thread.

SkillsMiddleware lists every skill statically; beyond ~20 skills the listing
itself starts to crowd the context. This subclass keeps progressive disclosure
but selects only the most relevant skills before the model ever sees the list.

**Selection semantics.** The selection is written to ``skills_metadata``, which
deepagents checkpoints, so it is computed on a thread's first turn and reused by
every later turn of that thread. Two consequences worth knowing:

- a skill installed in the hub mid-thread becomes visible in the next *thread*,
  not the next message;
- a task's skills stay available across its follow-up turns, where re-selecting
  on a low-signal message ("yes, go on") would drop the very skills the task is
  following.

That is the deliberate trade. Re-selecting every turn would make the first
consequence disappear and the second one appear.

Selection is pluggable via SkillSelector — mirror of langchain's
LLMToolSelectorMiddleware idea, applied to skills. Two selectors ship here:
an LLM selector (cheap model picks from the catalog) and an embedding
selector (cosine similarity over name+description; the server's pgvector
store is the production-grade version of this).

User-tier skills (skill hub): pass ``user_skills_loader``
and the middleware materializes the caller's DB-stored skills into the run's
ephemeral state filesystem under ``/user-skills/`` before listing. That path
is appended to ``sources`` automatically, so user skills override same-named
builtin ones (later source wins). Scripts land in StateBackend, not the pod
disk — they are inert unless the graph deliberately wires an executor.

deepagents' skill API is reached only through `shared/deepagents_skills.py`.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import structlog
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from agent_server.contracts import configurable_user_id
from shared.deepagents_skills import (
    SkillMetadata,
    SkillsMiddleware,
    SkillsStateUpdate,
    alist_skills_with_errors,
)
from shared.models import load_chat_model

logger = structlog.getLogger(__name__)

USER_SKILLS_PATH = "/user-skills/"

# Loader shape: user_id -> [{name, files: {path: text}}] (plain dicts, so
# graphs never import the server's domain models).
UserSkillsLoader = Callable[[str], Awaitable[list[dict[str, Any]]]]

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
        user_skills_loader: optional hub loader; the caller's user-tier skills
            are materialized under /user-skills/ before listing
    """

    def __init__(
        self,
        *,
        selector: SkillSelector,
        top_k: int = 5,
        always_include: Sequence[str] = (),
        user_skills_loader: UserSkillsLoader | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._selector = selector
        self._top_k = top_k
        self._always_include = tuple(always_include)
        self._user_skills_loader = user_skills_loader
        if user_skills_loader is not None and USER_SKILLS_PATH not in self.sources:
            # Last source wins on name collision — user tier overrides builtin.
            self.sources.append(USER_SKILLS_PATH)
            self.source_labels.append("User")

    async def _materialize_user_skills(self, config: RunnableConfig) -> None:
        """Copy the caller's hub skills into the run's state filesystem."""
        user_id = configurable_user_id(config)
        loader = self._user_skills_loader
        if not user_id or loader is None:
            return
        try:
            skills = await loader(user_id)
        except Exception as e:  # hub outage must degrade to builtin-only, never break a run
            logger.warning("user_skills_load_failed", user_id=user_id, error=str(e))
            return
        for skill in skills:
            for path, content in skill.get("files", {}).items():
                await self._backend.awrite(f"{USER_SKILLS_PATH}{skill['name']}/{path}", content)
        if skills:
            logger.info("user_skills_materialized", user_id=user_id, names=[s["name"] for s in skills])

    async def _load_all_skills(self) -> tuple[list[SkillMetadata], list[str]]:
        """The catalog plus per-source load errors, so a broken source is audible."""
        skills: list[SkillMetadata] = []
        load_errors: list[str] = []
        for source in self.sources:
            source_skills, source_error = await alist_skills_with_errors(self._backend, source)
            if source_error is not None:
                load_errors.append(source_error)
            skills.extend(source_skills)
        # Later sources override earlier ones by name (deepagents semantics).
        by_name: dict[str, SkillMetadata] = {}
        for skill in skills:
            by_name[skill["name"]] = skill
        return list(by_name.values()), load_errors

    async def _select(self, query: str, all_skills: list[SkillMetadata]) -> list[SkillMetadata]:
        """Top-k for this request.

        A selector outage degrades to the full catalog — the pre-router
        behaviour, which keeps the model able to do the task. The catalog is
        bounded, so the cost of failing open is context, not correctness.
        """
        if not query:
            return all_skills[: self._top_k]
        try:
            return await self._selector.select(query, all_skills)
        except Exception as e:  # a selector outage must never fail the run
            logger.warning("skill_selector_failed", error=str(e), skills=len(all_skills))
            return all_skills

    # The parent's abefore_agent is typed for SkillsState; ours deliberately
    # widens to dict (LSP contravariance) — same shape the parent itself
    # suppresses with the same annotation.
    async def abefore_agent(  # ty: ignore[invalid-method-override]
        self, state: dict[str, Any], runtime: Runtime, config: RunnableConfig
    ) -> SkillsStateUpdate | None:
        """Seed this thread's skill selection (see the module docstring)."""
        if "skills_metadata" in state:
            return None

        if self._user_skills_loader is not None:
            await self._materialize_user_skills(config)

        all_skills, load_errors = await self._load_all_skills()
        if load_errors:
            logger.warning("skill_sources_failed", count=len(load_errors), errors=load_errors)

        if not all_skills:
            return SkillsStateUpdate(skills_metadata=[], skills_load_errors=load_errors)

        selected = await self._select(_latest_user_text(state), all_skills)

        # always_include entries survive every filter and do not consume the
        # top_k budget — that is what "always include" has to mean.
        pinned = [s for s in all_skills if s["name"] in self._always_include]
        pinned_names = {s["name"] for s in pinned}
        skills = [*pinned, *[s for s in selected if s["name"] not in pinned_names][: self._top_k]]

        logger.info("skill_router_selected", count=len(skills), names=[s["name"] for s in skills])
        return SkillsStateUpdate(skills_metadata=skills, skills_load_errors=load_errors)


def _latest_user_text(state: dict[str, Any]) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage) or (isinstance(message, dict) and message.get("role") == "user"):
            content = message.content if isinstance(message, HumanMessage) else message.get("content", "")
            return content if isinstance(content, str) else ""
    return ""
