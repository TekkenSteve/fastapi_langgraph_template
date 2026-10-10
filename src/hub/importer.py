"""Skill import parsing: raw bytes → validated install payload (business rules).

Fetching lives in ``infra/skill_fetcher.py``; this module owns the rules:
a payload is either a raw UTF-8 SKILL.md or a zip holding exactly one skill
directory (SKILL.md at the zip root or in one top-level folder).
"""

import io
import stat
import zipfile
from collections.abc import Awaitable, Callable

from hub.models import (
    MAX_FILE_BYTES,
    MAX_FILES_PER_SKILL,
    MAX_TOTAL_BYTES,
    SKILL_MD,
    SkillFileInput,
    SkillValidationError,
)

# Injected into SkillService.import_from_url; infra.fetch_bytes is the default.
SkillFetcher = Callable[..., Awaitable[bytes]]

# POSIX file-type bits inside ZipInfo.external_attr (upper 16 bits). Archives
# written by Python carry mode bits without a type field, so a zero type is
# accepted; a present type must be a regular file.
_MODE_TYPE_MASK = 0o170000


def parse_import_payload(data: bytes) -> list[SkillFileInput]:
    """Interpret downloaded bytes as a skill (raw SKILL.md or zip archive)."""
    if data.startswith(b"PK\x03\x04"):
        return _unpack_zip(data)
    try:
        return [SkillFileInput(path=SKILL_MD, content=data.decode("utf-8"))]
    except UnicodeDecodeError as e:
        raise SkillValidationError("Import is neither a zip archive nor a UTF-8 SKILL.md") from e


def _reject_oversized_entries(entries: list[zipfile.ZipInfo]) -> None:
    """Refuse a zip whose declared entries break the install caps.

    Runs before anything is decompressed: a small archive that declares
    hundreds of megabytes (or thousands of entries) is refused without
    allocating for it. The read loop then enforces the real per-file cap, so a
    header that lies about its size cannot get past this either.
    """
    if len(entries) > MAX_FILES_PER_SKILL:
        raise SkillValidationError(f"Zip has more than {MAX_FILES_PER_SKILL} files")
    declared_total = 0
    for info in entries:
        mode = info.external_attr >> 16
        if mode & _MODE_TYPE_MASK and not stat.S_ISREG(mode):
            raise SkillValidationError(f"Zip entry {info.filename!r} is not a regular file")
        if info.file_size > MAX_FILE_BYTES:
            raise SkillValidationError(f"Zip entry {info.filename!r} exceeds {MAX_FILE_BYTES} bytes uncompressed")
        declared_total += info.file_size
        if declared_total > MAX_TOTAL_BYTES:
            raise SkillValidationError(f"Zip declares more than {MAX_TOTAL_BYTES} bytes uncompressed")


def _unpack_zip(data: bytes) -> list[SkillFileInput]:
    """Extract one skill directory from a zip into relative file inputs."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise SkillValidationError("Import is not a valid zip archive") from e

    entries = [i for i in archive.infolist() if not i.is_dir()]
    _reject_oversized_entries(entries)
    # The skill root is the directory containing SKILL.md — either the zip
    # root or a single top-level folder. More nesting is ambiguous: reject.
    roots = {i.filename[: -len(SKILL_MD)].rstrip("/") for i in entries if i.filename.endswith(SKILL_MD)}
    roots = {r for r in roots if r == "" or "/" not in r}
    if len(roots) != 1:
        raise SkillValidationError("Zip must contain exactly one SKILL.md (at root or in one top-level folder)")
    root = roots.pop()
    prefix = f"{root}/" if root else ""

    files: list[SkillFileInput] = []
    read_total = 0
    for info in entries:
        if not info.filename.startswith(prefix):
            continue  # outside the skill root (e.g. a README next to the folder)
        rel = info.filename[len(prefix) :]
        with archive.open(info) as member:
            raw = member.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise SkillValidationError(f"File {rel!r} exceeds {MAX_FILE_BYTES} bytes")
        read_total += len(raw)
        if read_total > MAX_TOTAL_BYTES:
            raise SkillValidationError(f"Zip contents exceed {MAX_TOTAL_BYTES} bytes")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise SkillValidationError(f"File {rel!r} is not UTF-8 text") from e
        files.append(SkillFileInput(path=rel, content=content))
    return files
