"""Unit tests for skill import parsing (raw bytes → install payload)."""

import io
import zipfile

import pytest

from hub.importer import parse_import_payload
from hub.models import SkillValidationError

SKILL_MD = "---\nname: imported\ndescription: from url\n---\n# Body\n"


def _zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def test_raw_skill_md_becomes_single_file() -> None:
    files = parse_import_payload(SKILL_MD.encode())
    assert len(files) == 1
    assert files[0].path == "SKILL.md"
    assert files[0].content == SKILL_MD


def test_zip_with_skill_at_root() -> None:
    files = parse_import_payload(_zip({"SKILL.md": SKILL_MD, "scripts/h.py": "print(1)"}))
    assert {f.path for f in files} == {"SKILL.md", "scripts/h.py"}


def test_zip_with_single_top_level_folder_strips_prefix() -> None:
    files = parse_import_payload(_zip({"my-skill/SKILL.md": SKILL_MD, "my-skill/scripts/h.py": "x"}))
    assert {f.path for f in files} == {"SKILL.md", "scripts/h.py"}


def test_zip_with_multiple_skill_roots_is_rejected() -> None:
    with pytest.raises(SkillValidationError, match="exactly one"):
        parse_import_payload(_zip({"a/SKILL.md": SKILL_MD, "b/SKILL.md": SKILL_MD}))


def test_zip_with_deep_nested_skill_is_rejected() -> None:
    with pytest.raises(SkillValidationError, match="exactly one"):
        parse_import_payload(_zip({"a/b/SKILL.md": SKILL_MD}))


def test_non_utf8_non_zip_is_rejected() -> None:
    with pytest.raises(SkillValidationError, match="neither"):
        parse_import_payload(b"\x89PNG junk")


def test_invalid_zip_is_rejected() -> None:
    with pytest.raises(SkillValidationError, match="zip"):
        parse_import_payload(b"PK\x03\x04 not really a zip")
