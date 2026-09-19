"""Unit tests for skill hub domain validation (Agent Skills spec + hub limits)."""

import pytest

from hub.models import (
    MAX_DESCRIPTION_CHARS,
    MAX_FILE_BYTES,
    MAX_FILES_PER_SKILL,
    SkillFileInput,
    SkillValidationError,
    parse_skill_md,
    validate_file_path,
    validate_skill_files,
)

VALID_SKILL_MD = "---\nname: my-skill\ndescription: does things\n---\n\n# My Skill\n"


def _file(path: str = "SKILL.md", content: str = VALID_SKILL_MD) -> SkillFileInput:
    return SkillFileInput(path=path, content=content)


# --- parse_skill_md --------------------------------------------------------


def test_parses_full_frontmatter() -> None:
    parsed = parse_skill_md("---\nname: a\ndescription: b\nlicense: MIT\nmetadata:\n  k: v\n---\n# body\n")
    assert parsed.name == "a"
    assert parsed.description == "b"
    assert parsed.license == "MIT"
    assert parsed.metadata == {"k": "v"}


def test_rejects_missing_frontmatter() -> None:
    with pytest.raises(SkillValidationError, match="frontmatter"):
        parse_skill_md("# just markdown\n")


def test_rejects_unclosed_frontmatter() -> None:
    with pytest.raises(SkillValidationError, match="not closed"):
        parse_skill_md("---\nname: a\ndescription: b\n")


def test_rejects_non_mapping_frontmatter() -> None:
    with pytest.raises(SkillValidationError, match="mapping"):
        parse_skill_md("---\n- a\n- b\n---\n")


@pytest.mark.parametrize("name", ["", "A-upper", "has_underscore", "-leading-hyphen", "x" * 65, "spaces in name"])
def test_rejects_invalid_names(name: str) -> None:
    with pytest.raises(SkillValidationError, match="name"):
        parse_skill_md(f"---\nname: {name!r}\ndescription: ok\n---\n")


def test_rejects_missing_description() -> None:
    with pytest.raises(SkillValidationError, match="description"):
        parse_skill_md("---\nname: ok-name\n---\n")


def test_rejects_oversized_description() -> None:
    with pytest.raises(SkillValidationError, match="description"):
        parse_skill_md(f"---\nname: ok-name\ndescription: {'x' * (MAX_DESCRIPTION_CHARS + 1)}\n---\n")


# --- validate_file_path ----------------------------------------------------


@pytest.mark.parametrize(
    "path", ["", "/abs/path", "..\\win", "../escape", "a//b", "./dot", "a/./b", "a/../b", "back\\slash"]
)
def test_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(SkillValidationError, match="path"):
        validate_file_path(path)


@pytest.mark.parametrize("path", ["SKILL.md", "scripts/helper.py", "a/b/c.txt"])
def test_accepts_safe_paths(path: str) -> None:
    validate_file_path(path)


# --- validate_skill_files --------------------------------------------------


def test_requires_skill_md_at_root() -> None:
    with pytest.raises(SkillValidationError, match="SKILL.md"):
        validate_skill_files([_file("docs/SKILL.md")])


def test_rejects_duplicate_paths() -> None:
    with pytest.raises(SkillValidationError, match="Duplicate"):
        validate_skill_files([_file(), _file("SKILL.md", VALID_SKILL_MD)])


def test_rejects_too_many_files() -> None:
    files = [_file()] + [_file(f"f{i}.txt", "x") for i in range(MAX_FILES_PER_SKILL)]
    with pytest.raises(SkillValidationError, match="max"):
        validate_skill_files(files)


def test_rejects_oversized_file() -> None:
    with pytest.raises(SkillValidationError, match="exceeds"):
        validate_skill_files([_file(), _file("big.txt", "x" * (MAX_FILE_BYTES + 1))])


def test_accepts_valid_payload_with_subdirectories() -> None:
    parsed = validate_skill_files([_file(), _file("scripts/helper.py", "print(1)\n")])
    assert parsed.name == "my-skill"
