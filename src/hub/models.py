"""Hub domain models: skills, MCP connections, and policy resource constants.

Skill models implement Agent Skills spec validation; connection models pin
the user tier to streamable_http with write-only credentials. Validation
lives here so the rules are transport-independent and unit-testable — the
services map ``*ValidationError`` to 422.

The hub owns its policy resource families (the platform's domain/policy.py
deliberately defines none — see its docstring).
"""

import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from agent_server.domain.policy import Permission, ResourceType

SKILL = ResourceType("skill")
MCP_CONNECTION = ResourceType("mcp_connection")

# Hub business verbs (policies are organized by business concept, not API
# CRUD — see docs/design/hub.md). The platform's generic verbs (READ etc.)
# live in agent_server.domain.policy.
INSTALL = Permission("install")
UNINSTALL = Permission("uninstall")


SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SKILL_MD = "SKILL.md"

# Hub limits (user tier is untrusted input; builtin skills are shipped code
# and not subject to these).
MAX_FILES_PER_SKILL = 50
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_DESCRIPTION_CHARS = 1024


class SkillValidationError(ValueError):
    """A skill payload violates the Agent Skills spec or hub limits."""


class SkillFileInput(BaseModel):
    """One file of an install payload, path relative to the skill root."""

    path: str = Field(..., description="Relative POSIX path, e.g. SKILL.md or scripts/helper.py")
    content: str = Field(..., description="UTF-8 text content")


class SkillFileView(BaseModel):
    """A file as returned by the API (text content, never raw bytes)."""

    path: str
    content: str


class SkillInstall(BaseModel):
    """Install/replace payload: a flat file list containing SKILL.md."""

    files: list[SkillFileInput] = Field(..., min_length=1)


class SkillSummary(BaseModel):
    """List view of an installed user-tier skill."""

    name: str
    description: str
    license: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tier: str = "user"
    created_at: datetime
    updated_at: datetime


class SkillDetail(SkillSummary):
    """Detail view including every file."""

    files: list[SkillFileView]


class SkillImportRequest(BaseModel):
    """Import from a URL pointing at a raw SKILL.md or a .zip archive."""

    url: str = Field(..., description="http(s) URL of a raw SKILL.md or .zip skill archive")


class ParsedSkillMd(BaseModel):
    """Frontmatter parsed out of a SKILL.md."""

    name: str
    description: str
    license: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def validate_file_path(path: str) -> None:
    """Reject anything that is not a safe relative POSIX path."""
    if not path or path.startswith("/") or "\\" in path:
        raise SkillValidationError(f"Invalid file path: {path!r}")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SkillValidationError(f"Invalid file path: {path!r}")


def parse_skill_md(content: str) -> ParsedSkillMd:
    """Parse and validate SKILL.md frontmatter per the Agent Skills spec."""
    if not content.startswith("---"):
        raise SkillValidationError("SKILL.md must start with YAML frontmatter (---)")
    end = content.find("\n---", 3)
    if end == -1:
        raise SkillValidationError("SKILL.md frontmatter is not closed (---)")
    try:
        data = yaml.safe_load(content[3:end])
    except yaml.YAMLError as e:
        raise SkillValidationError(f"SKILL.md frontmatter is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise SkillValidationError("SKILL.md frontmatter must be a mapping")

    name = data.get("name")
    if not isinstance(name, str) or not SKILL_NAME_PATTERN.match(name):
        raise SkillValidationError("frontmatter 'name' must be 1-64 chars of lowercase letters, digits, hyphens")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillValidationError("frontmatter 'description' is required")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillValidationError(f"frontmatter 'description' exceeds {MAX_DESCRIPTION_CHARS} chars")
    license_ = data.get("license")
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise SkillValidationError("frontmatter 'metadata' must be a mapping")
    return ParsedSkillMd(
        name=name,
        description=description,
        license=license_ if isinstance(license_, str) else None,
        metadata=metadata,
    )


def validate_skill_files(files: list[SkillFileInput]) -> ParsedSkillMd:
    """Validate a whole install payload; returns the parsed SKILL.md frontmatter."""
    if len(files) > MAX_FILES_PER_SKILL:
        raise SkillValidationError(f"Skill has {len(files)} files, max is {MAX_FILES_PER_SKILL}")

    seen: set[str] = set()
    total = 0
    skill_md: SkillFileInput | None = None
    for f in files:
        validate_file_path(f.path)
        if f.path in seen:
            raise SkillValidationError(f"Duplicate file path: {f.path!r}")
        seen.add(f.path)
        size = len(f.content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise SkillValidationError(f"File {f.path!r} exceeds {MAX_FILE_BYTES} bytes")
        total += size
        if f.path == SKILL_MD:
            skill_md = f
    if total > MAX_TOTAL_BYTES:
        raise SkillValidationError(f"Skill totals {total} bytes, max is {MAX_TOTAL_BYTES}")
    if skill_md is None:
        raise SkillValidationError("SKILL.md is required at the skill root")
    return parse_skill_md(skill_md.content)


MCP_CONNECTION_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_HEADERS = 20

USER_TRANSPORT = "streamable_http"


class McpConnectionValidationError(ValueError):
    """A connection payload violates hub rules."""


def validate_connection_name(name: str) -> None:
    """Names share the registry namespace (user connections override by name)."""
    if not MCP_CONNECTION_NAME_PATTERN.match(name):
        raise McpConnectionValidationError(
            "name must be 1-64 chars of lowercase letters, digits, hyphens, starting with a letter or digit"
        )


def validate_connection_url(url: str) -> None:
    """http(s) only — no file:, no stdio, no schemeless strings."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise McpConnectionValidationError("url must be an absolute http(s) URL")


class McpConnectionCreate(BaseModel):
    """Create payload. ``transport`` is declared and pinned to the only value
    the user tier supports, so a future second transport is a conscious API
    change, not a silent acceptance.

    ``auth_type``: ``none`` (no credentials), ``headers`` (static headers,
    write-only), or ``oauth`` (per-user SDK-driven flow — the user completes
    authorization in their browser on first use; see hub/oauth/).
    """

    name: str
    transport: Literal["streamable_http"] = USER_TRANSPORT
    url: str
    auth_type: Literal["none", "headers", "oauth"] = "none"
    headers: dict[str, str] = Field(default_factory=dict, description="Credentials; write-only, never returned")

    @field_validator("headers")
    @classmethod
    def _cap_headers(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > MAX_HEADERS:
            raise ValueError(f"at most {MAX_HEADERS} headers")
        return v

    @model_validator(mode="after")
    def _auth_type_matches_headers(self) -> "McpConnectionCreate":
        if self.auth_type == "headers" and not self.headers:
            raise ValueError("auth_type 'headers' requires at least one header")
        if self.auth_type != "headers" and self.headers:
            raise ValueError(f"headers are not allowed with auth_type {self.auth_type!r}")
        return self


class McpConnectionUpdate(BaseModel):
    """Patch payload. ``headers`` replaces the whole set when present."""

    url: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None


class McpConnectionView(BaseModel):
    """Response model — header *keys* only, values never leave the server."""

    name: str
    transport: str
    url: str
    auth_type: str
    header_keys: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime
