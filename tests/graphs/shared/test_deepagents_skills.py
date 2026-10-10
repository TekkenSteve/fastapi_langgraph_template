"""Contract test for the deepagents skill adapter.

These assertions are the tripwire for upstream changes: the adapter exists so a
moved symbol fails here, with this file named, instead of surfacing as a run
that quietly loads no skills.
"""

import inspect
from typing import get_type_hints

import pytest

from shared import deepagents_skills
from shared.monty_sandbox import MontySandboxBackend


def test_the_adapter_exposes_exactly_what_graphs_consume() -> None:
    assert deepagents_skills.__all__ == [
        "SkillMetadata",
        "SkillsMiddleware",
        "SkillsStateUpdate",
        "alist_skills_with_errors",
    ]
    for name in deepagents_skills.__all__:
        assert hasattr(deepagents_skills, name), f"deepagents no longer provides {name}"


def test_alist_skills_with_errors_keeps_the_error_half() -> None:
    """The public loader discards the error; we rely on the private variant."""
    signature = inspect.signature(deepagents_skills.alist_skills_with_errors)
    assert list(signature.parameters) == ["backend", "source_path"]
    assert inspect.iscoroutinefunction(deepagents_skills.alist_skills_with_errors)


def test_skills_state_update_still_carries_the_load_errors_channel() -> None:
    hints = get_type_hints(deepagents_skills.SkillsStateUpdate, include_extras=True)
    assert "skills_metadata" in hints
    assert "skills_load_errors" in hints


def test_skill_metadata_has_the_fields_the_router_reads() -> None:
    hints = get_type_hints(deepagents_skills.SkillMetadata)
    assert {"name", "description"} <= set(hints)


async def test_alist_skills_with_errors_reports_a_missing_source() -> None:
    """A source that cannot be listed must come back as an error, not as empty."""
    from deepagents.backends.protocol import LsResult

    class _UnreadableSource(MontySandboxBackend):
        def ls(self, path: str) -> LsResult:
            return LsResult(error="permission denied")

    skills, error = await deepagents_skills.alist_skills_with_errors(_UnreadableSource(), "/skills/")

    assert skills == []
    assert error and "permission denied" in error, "a failed source must produce a skills_load_errors entry"


@pytest.mark.parametrize("field", ["path", "is_dir"])
def test_file_info_still_models_directories(field: str) -> None:
    """The monty visibility bug came from an entry shape deepagents filters on."""
    from deepagents.backends.protocol import FileInfo, LsResult

    assert field in get_type_hints(FileInfo)
    assert "entries" in get_type_hints(LsResult)
