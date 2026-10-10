"""Unit tests for skill import parsing (raw bytes → install payload)."""

import io
import zipfile

import pytest

from hub.importer import parse_import_payload
from hub.models import (
    MAX_FILE_BYTES,
    MAX_FILES_PER_SKILL,
    SkillValidationError,
)

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


# --- decompression caps -------------------------------------------------------
# The caps are enforced from the archive's declared sizes before anything is
# read, so a small upload that declares a huge payload is refused without
# allocating for it; the read loop then re-checks the real bytes.


def test_zip_entry_over_the_per_file_cap_is_refused() -> None:
    payload = b"a" * (MAX_FILE_BYTES + 1)
    with pytest.raises(SkillValidationError, match="uncompressed"):
        parse_import_payload(_zip({"SKILL.md": SKILL_MD, "big.txt": payload.decode()}))


def test_zip_over_the_total_cap_is_refused() -> None:
    chunk = "b" * (MAX_FILE_BYTES - 1)
    entries = {"SKILL.md": SKILL_MD, **{f"f{i}.txt": chunk for i in range(MAX_FILES_PER_SKILL - 1)}}
    with pytest.raises(SkillValidationError, match="more than|exceeds"):
        parse_import_payload(_zip(entries))


def test_zip_with_too_many_entries_is_refused() -> None:
    entries = {"SKILL.md": SKILL_MD, **{f"f{i}.txt": "x" for i in range(MAX_FILES_PER_SKILL)}}
    with pytest.raises(SkillValidationError, match="more than"):
        parse_import_payload(_zip(entries))


def test_declared_sizes_reject_a_bomb_without_reading_it() -> None:
    """The pre-check works off infolist() alone — the entries are never opened."""
    from hub.importer import _reject_oversized_entries

    bomb = zipfile.ZipInfo("SKILL.md")
    bomb.file_size = MAX_FILE_BYTES + 1
    with pytest.raises(SkillValidationError, match="uncompressed"):
        _reject_oversized_entries([bomb])


def test_symlink_entry_is_refused() -> None:
    """A symlink in the archive would let a skill point the FS elsewhere."""
    import stat

    link = zipfile.ZipInfo("SKILL.md")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    link.file_size = 10
    from hub.importer import _reject_oversized_entries

    with pytest.raises(SkillValidationError, match="not a regular file"):
        _reject_oversized_entries([link])
