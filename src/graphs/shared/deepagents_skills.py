"""The one place that touches deepagents' skill API.

``skill_router`` needs three public types plus one *private* loader
(``_alist_skills_with_errors``) that deepagents does not export under a stable
name. Reaching for it from several modules would scatter that risk; instead it
is imported exactly once, here, next to a contract test that pins the shapes we
depend on (``tests/graphs/shared/test_deepagents_skills.py``) and a version
bound in ``pyproject.toml``.

If an upgrade moves or renames any of this, the adapter import fails — loudly,
at test/startup time, with this file named — instead of a run quietly losing
its skill catalog or, worse, silently loading no skills at all.

Only the skill surface lives here. ``deepagents.backends.protocol`` is a public
path and ``monty_sandbox`` imports it directly; the contract test still pins the
``LsResult``/``FileInfo`` shapes both sides rely on, because that is where the
monty visibility bug came from.
"""

from typing import Any

from deepagents.middleware.skills import (
    SkillMetadata,
    SkillsMiddleware,
    SkillsStateUpdate,
    _alist_skills_with_errors,
)

__all__ = [
    "SkillMetadata",
    "SkillsMiddleware",
    "SkillsStateUpdate",
    "alist_skills_with_errors",
]


async def alist_skills_with_errors(backend: Any, source_path: str) -> tuple[list[SkillMetadata], str | None]:
    """List one source's skills, keeping the source-level error.

    The public ``_alist_skills`` drops the error half on the floor, which is how
    a malformed skill directory turns into "no skills" with nothing in the logs.
    deepagents' own middleware uses this variant to feed ``skills_load_errors``;
    we do the same.
    """
    return await _alist_skills_with_errors(backend, source_path)
