"""Skill import parsing: raw bytes → validated install payload (business rules).

Fetching lives in ``infra/skill_fetcher.py``; this module owns the rules:
a payload is either a raw UTF-8 SKILL.md or a zip holding exactly one skill
directory (SKILL.md at the zip root or in one top-level folder).
"""

import io
import zipfile
from collections.abc import Awaitable, Callable

from hub.models import SKILL_MD, SkillFileInput, SkillValidationError

# Injected into SkillService.import_from_url; infra.fetch_bytes is the default.
SkillFetcher = Callable[..., Awaitable[bytes]]


def parse_import_payload(data: bytes) -> list[SkillFileInput]:
    """Interpret downloaded bytes as a skill (raw SKILL.md or zip archive)."""
    if data.startswith(b"PK\x03\x04"):
        return _unpack_zip(data)
    try:
        return [SkillFileInput(path=SKILL_MD, content=data.decode("utf-8"))]
    except UnicodeDecodeError as e:
        raise SkillValidationError("Import is neither a zip archive nor a UTF-8 SKILL.md") from e


def _unpack_zip(data: bytes) -> list[SkillFileInput]:
    """Extract one skill directory from a zip into relative file inputs."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise SkillValidationError("Import is not a valid zip archive") from e

    entries = [i for i in archive.infolist() if not i.is_dir()]
    # The skill root is the directory containing SKILL.md — either the zip
    # root or a single top-level folder. More nesting is ambiguous: reject.
    roots = {i.filename[: -len(SKILL_MD)].rstrip("/") for i in entries if i.filename.endswith(SKILL_MD)}
    roots = {r for r in roots if r == "" or "/" not in r}
    if len(roots) != 1:
        raise SkillValidationError("Zip must contain exactly one SKILL.md (at root or in one top-level folder)")
    root = roots.pop()
    prefix = f"{root}/" if root else ""

    files: list[SkillFileInput] = []
    for info in entries:
        if not info.filename.startswith(prefix):
            continue  # outside the skill root (e.g. a README next to the folder)
        rel = info.filename[len(prefix) :]
        try:
            content = archive.read(info).decode("utf-8")
        except UnicodeDecodeError as e:
            raise SkillValidationError(f"File {rel!r} is not UTF-8 text") from e
        files.append(SkillFileInput(path=rel, content=content))
    return files
