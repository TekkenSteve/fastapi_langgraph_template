"""Hub services: skill + MCP connection orchestration.

Request-scoped services composed by the api providers: repository port +
policy engine + caller identity. Tenant isolation shows up twice on purpose:
repositories are always queried owner-scoped (cross-user rows are 404, never
403 — existence must not leak), and the policy engine re-checks every
decision in subject–permission–object form (where a future ReBAC engine
answers differently without this code changing).
"""

from urllib.parse import urlparse

import structlog
from fastapi import HTTPException

from agent_server.auth.policy import PolicyEngine
from agent_server.domain.policy import CREATE, DELETE, READ, SEARCH, UPDATE, ResourceRef
from agent_server.domain.user import User
from agent_server.infra.skill_fetcher import SkillFetchError, fetch_bytes
from hub.config import hub_settings
from hub.db import McpConnection as McpConnectionORM
from hub.db import Skill as SkillORM
from hub.importer import SkillFetcher, parse_import_payload
from hub.models import (
    INSTALL,
    MCP_CONNECTION,
    SKILL,
    UNINSTALL,
    McpConnectionCreate,
    McpConnectionUpdate,
    McpConnectionValidationError,
    McpConnectionView,
    SkillDetail,
    SkillFileView,
    SkillInstall,
    SkillSummary,
    SkillValidationError,
    validate_connection_name,
    validate_connection_url,
    validate_skill_files,
)
from hub.repositories import McpConnectionRepository, SkillRepository

logger = structlog.getLogger(__name__)


def _to_summary(row: SkillORM) -> SkillSummary:
    return SkillSummary(
        name=row.name,
        description=row.description,
        license=row.license,
        metadata=row.metadata_dict or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SkillService:
    """Request-scoped skill hub service (repository + policy + caller)."""

    def __init__(self, skills: SkillRepository, policy: PolicyEngine, user: User) -> None:
        self._skills = skills
        self._policy = policy
        self._user = user

    async def _get_owned(self, name: str) -> SkillORM:
        row = await self._skills.get_for_owner(self._user.identity, name)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Skill {name!r} not found")
        return row

    async def list_mine(self) -> list[SkillSummary]:
        """Every skill visible to the caller, per the policy access filter."""
        visible = await self._policy.access_filter(self._user, SEARCH, SKILL)
        if visible.allow_all:
            rows = await self._skills.list_all()
        elif visible.object_ids is not None:
            rows = await self._skills.list_by_names(visible.object_ids)
        else:
            rows = await self._skills.list_for_owner(visible.owner_id or self._user.identity)
        return [_to_summary(r) for r in rows]

    async def get(self, name: str) -> SkillDetail:
        """One skill with all its files."""
        row = await self._get_owned(name)
        await self._policy.require(self._user, READ, ResourceRef(SKILL, name, owner_id=row.user_id))
        files = await self._skills.list_files(row.skill_id)
        # Content is UTF-8 by construction (install/import reject anything else).
        views = [SkillFileView(path=f.path, content=f.content.decode("utf-8")) for f in files]
        return SkillDetail(**_to_summary(row).model_dump(), files=views)

    async def install(self, payload: SkillInstall) -> SkillDetail:
        """Install or fully replace a skill (upsert by (owner, name)).

        The skill name comes from the SKILL.md frontmatter — the payload has
        no separate name field, so there is nothing that could disagree.
        """
        try:
            parsed = validate_skill_files(payload.files)
        except SkillValidationError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        await self._policy.require(
            self._user,
            INSTALL,
            ResourceRef(SKILL, parsed.name, owner_id=self._user.identity),
        )

        existed = await self._skills.get_for_owner(self._user.identity, parsed.name)
        row = await self._skills.save(
            self._user.identity,
            parsed.name,
            description=parsed.description,
            license=parsed.license,
            metadata=parsed.metadata,
            files={f.path: f.content.encode("utf-8") for f in payload.files},
        )
        logger.info("skill_replaced" if existed else "skill_installed", user=self._user.identity, skill=parsed.name)
        views = [SkillFileView(path=f.path, content=f.content) for f in payload.files]
        return SkillDetail(**_to_summary(row).model_dump(), files=views)

    async def replace(self, name: str, payload: SkillInstall) -> SkillDetail:
        """Full replace via PUT. The path name must match the frontmatter name —
        a mismatch is a 422, never a silent rename. 404 when the skill is absent
        (PUT-to-create would hide typos; install lives on POST)."""
        try:
            parsed = validate_skill_files(payload.files)
        except SkillValidationError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        if parsed.name != name:
            raise HTTPException(
                status_code=422, detail=f"Path name {name!r} does not match frontmatter name {parsed.name!r}"
            )
        await self._get_owned(name)
        return await self.install(payload)

    async def uninstall(self, name: str) -> None:
        """Remove a skill and its files (files cascade)."""
        row = await self._get_owned(name)
        await self._policy.require(self._user, UNINSTALL, ResourceRef(SKILL, name, owner_id=row.user_id))
        await self._skills.delete(row)
        logger.info("skill_uninstalled", user=self._user.identity, skill=name)

    async def import_from_url(self, url: str, *, fetcher: SkillFetcher = fetch_bytes) -> SkillDetail:
        """Install from a URL: a raw SKILL.md, or a .zip holding one skill directory."""
        try:
            data = await fetcher(
                url, max_bytes=hub_settings.SKILL_IMPORT_MAX_BYTES, timeout=hub_settings.SKILL_IMPORT_TIMEOUT_SECS
            )
            files = parse_import_payload(data)
        except (SkillFetchError, SkillValidationError) as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        detail = await self.install(SkillInstall(files=files))
        logger.info("skill_imported", user=self._user.identity, skill=detail.name, url=url)
        return detail


def _to_view(row: McpConnectionORM) -> McpConnectionView:
    """Response shape — header keys only, values never leave the server."""
    return McpConnectionView(
        name=row.name,
        transport=row.transport,
        url=row.url,
        auth_type=row.auth_type,
        header_keys=sorted((row.headers or {}).keys()),
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class McpConnectionService:
    """Request-scoped MCP connection hub service (repository + policy + caller)."""

    def __init__(self, connections: McpConnectionRepository, policy: PolicyEngine, user: User) -> None:
        self._connections = connections
        self._policy = policy
        self._user = user

    def _validate(self, name: str, url: str) -> None:
        """Domain rules plus the ops domain allowlist, mapped to 422."""
        try:
            validate_connection_name(name)
            validate_connection_url(url)
        except McpConnectionValidationError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        allowed = hub_settings.allowed_mcp_domains()
        if allowed and urlparse(url).hostname not in allowed:
            raise HTTPException(status_code=422, detail=f"url host is not in MCP_USER_ALLOWED_DOMAINS: {allowed}")

    async def _get_owned(self, name: str) -> McpConnectionORM:
        row = await self._connections.get_for_owner(self._user.identity, name)
        if row is None:
            raise HTTPException(status_code=404, detail=f"MCP connection {name!r} not found")
        return row

    def _ref(self, row: McpConnectionORM) -> ResourceRef:
        return ResourceRef(MCP_CONNECTION, row.name, owner_id=row.user_id)

    async def list_mine(self) -> list[McpConnectionView]:
        """Every connection the caller owns, ordered by name."""
        visible = await self._policy.access_filter(self._user, SEARCH, MCP_CONNECTION)
        if visible.allow_all:
            rows = await self._connections.list_for_owner(self._user.identity)  # hub has no cross-user list view
        else:
            rows = await self._connections.list_for_owner(visible.owner_id or self._user.identity)
        return [_to_view(r) for r in rows]

    async def get(self, name: str) -> McpConnectionView:
        """One connection by name."""
        row = await self._get_owned(name)
        await self._policy.require(self._user, READ, self._ref(row))
        return _to_view(row)

    async def create(self, payload: McpConnectionCreate) -> McpConnectionView:
        """Register a new connection. Name collisions with the deployment
        registry are allowed on purpose — the user connection wins at resolve
        time (user > registry precedence, docs/design/hub.md)."""
        self._validate(payload.name, payload.url)
        await self._policy.require(
            self._user,
            CREATE,
            ResourceRef(MCP_CONNECTION, payload.name, owner_id=self._user.identity),
        )
        if await self._connections.get_for_owner(self._user.identity, payload.name) is not None:
            raise HTTPException(status_code=409, detail=f"MCP connection {payload.name!r} already exists")

        row = await self._connections.insert(
            self._user.identity,
            payload.name,
            transport=payload.transport,
            url=payload.url,
            auth_type=payload.auth_type,
            headers=payload.headers,
        )
        logger.info("mcp_connection_created", user=self._user.identity, name=payload.name)
        return _to_view(row)

    async def update(self, name: str, payload: McpConnectionUpdate) -> McpConnectionView:
        """Patch url / headers / enabled. ``headers`` replaces the whole set."""
        row = await self._get_owned(name)
        await self._policy.require(self._user, UPDATE, self._ref(row))
        if payload.url is not None:
            self._validate(row.name, payload.url)
            row.url = payload.url
        if payload.headers is not None:
            row.headers = payload.headers
        if payload.enabled is not None:
            row.enabled = payload.enabled
        await self._connections.save(row)
        logger.info("mcp_connection_updated", user=self._user.identity, name=name)
        return _to_view(row)

    async def delete(self, name: str) -> None:
        """Remove a connection."""
        row = await self._get_owned(name)
        await self._policy.require(self._user, DELETE, self._ref(row))
        await self._connections.delete(row)
        logger.info("mcp_connection_deleted", user=self._user.identity, name=name)
