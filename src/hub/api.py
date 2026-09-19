"""Hub REST API: user-tier skills and MCP connections.

Application-layer surface, merged into the server through ``http.app`` in
langgraph.json (same path as shop/ml) — it is not part of the Agent Protocol
and deliberately does not touch the protocol server's router table or auth
registry. Authentication comes from the framework's auth_dependency;
authorization from the policy engine inside the services.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agent_server.auth.deps import auth_dependency, get_current_user
from agent_server.auth.policy import PolicyEngine, get_policy_engine
from agent_server.domain.user import User
from agent_server.repo.orm import get_session
from hub import apps_host
from hub.models import (
    McpConnectionCreate,
    McpConnectionUpdate,
    McpConnectionView,
    SkillDetail,
    SkillImportRequest,
    SkillInstall,
    SkillSummary,
)
from hub.oauth.flow import complete_from_browser
from hub.repositories import SqlAlchemyMcpConnectionRepository, SqlAlchemySkillRepository
from hub.services import McpConnectionService, SkillService

logger = structlog.getLogger(__name__)

router = APIRouter(tags=["hub"], dependencies=auth_dependency)

# The OAuth callback is a browser redirect from the external auth server — it
# carries no user credentials, only the flow's state/code. It stays public and
# is protected by the unguessable state instead (single-use, short TTL).
public_router = APIRouter(tags=["hub"])


@public_router.get("/hub/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(code: str, state: str) -> HTMLResponse:
    """Park a browser-delivered OAuth code for the waiting run to pick up."""
    if not await complete_from_browser(state, code):
        raise HTTPException(status_code=400, detail="Unknown or expired OAuth state")
    return HTMLResponse("<p>Authorization complete — you can close this tab and return to the agent.</p>")


# --- providers (DI composition roots; tests override these) -----------------


def get_skill_service(
    session: AsyncSession = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy_engine),
    user: User = Depends(get_current_user),
) -> SkillService:
    """Compose the request-scoped skill service."""
    return SkillService(SqlAlchemySkillRepository(session), policy, user)


def get_mcp_connection_service(
    session: AsyncSession = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy_engine),
    user: User = Depends(get_current_user),
) -> McpConnectionService:
    """Compose the request-scoped connection service."""
    return McpConnectionService(SqlAlchemyMcpConnectionRepository(session), policy, user)


# --- MCP Apps host proxy (request-scoped sessions, OAuth-aware) ---------------
# Lets the frontend drive a connection's resources and app tools directly —
# the UI half of MCP Apps (SEP-1865). Model-facing tool lists come from the
# graph side; app-only tools live here.


class ToolCallRequest(BaseModel):
    """Payload for the tools/call proxy."""

    tool_name: str
    args: dict = Field(default_factory=dict)


@router.post("/hub/mcp/{name}/tools/list")
async def mcp_tools_list(
    name: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """List a connection's tools with metadata (visibility, ui)."""
    repo = SqlAlchemyMcpConnectionRepository(session)
    return await apps_host.list_tools(repo, user.identity, name)


@router.post("/hub/mcp/{name}/tools/call")
async def mcp_tools_call(
    name: str,
    payload: ToolCallRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """Call one tool by name through the caller's connection."""
    repo = SqlAlchemyMcpConnectionRepository(session)
    return await apps_host.call_tool(repo, user.identity, name, payload.tool_name, payload.args)


@router.post("/hub/mcp/{name}/resources/list")
async def mcp_resources_list(
    name: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """List a connection's resources and resource templates."""
    repo = SqlAlchemyMcpConnectionRepository(session)
    return await apps_host.list_resources(repo, user.identity, name)


class ResourceReadRequest(BaseModel):
    """Payload for the resources/read proxy."""

    uri: str


@router.post("/hub/mcp/{name}/resources/read")
async def mcp_resources_read(
    name: str,
    payload: ResourceReadRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """Read one resource by URI (text contents only)."""
    repo = SqlAlchemyMcpConnectionRepository(session)
    return await apps_host.read_resource(repo, user.identity, name, payload.uri)


# --- skills ------------------------------------------------------------------


@router.get("/skills", response_model=list[SkillSummary])
async def list_skills(service: SkillService = Depends(get_skill_service)) -> list[SkillSummary]:
    """List every skill the caller has installed."""
    return await service.list_mine()


@router.post("/skills", response_model=SkillDetail, status_code=201)
async def install_skill(
    payload: SkillInstall,
    service: SkillService = Depends(get_skill_service),
) -> SkillDetail:
    """Install a skill (or fully replace one with the same name)."""
    return await service.install(payload)


@router.post("/skills/import", response_model=SkillDetail, status_code=201)
async def import_skill(
    payload: SkillImportRequest,
    service: SkillService = Depends(get_skill_service),
) -> SkillDetail:
    """Install from a URL pointing at a raw SKILL.md or a .zip archive."""
    return await service.import_from_url(payload.url)


@router.get("/skills/{name}", response_model=SkillDetail)
async def get_skill(
    name: str,
    service: SkillService = Depends(get_skill_service),
) -> SkillDetail:
    """Get one skill with all its files."""
    return await service.get(name)


@router.put("/skills/{name}", response_model=SkillDetail)
async def replace_skill(
    name: str,
    payload: SkillInstall,
    service: SkillService = Depends(get_skill_service),
) -> SkillDetail:
    """Replace a skill's content (path name must match the frontmatter name)."""
    return await service.replace(name, payload)


@router.delete("/skills/{name}", status_code=204)
async def uninstall_skill(
    name: str,
    service: SkillService = Depends(get_skill_service),
) -> Response:
    """Remove a skill and its files."""
    await service.uninstall(name)
    return Response(status_code=204)


# --- mcp connections ----------------------------------------------------------


@router.get("/mcp-connections", response_model=list[McpConnectionView])
async def list_connections(
    service: McpConnectionService = Depends(get_mcp_connection_service),
) -> list[McpConnectionView]:
    """List every connection the caller owns."""
    return await service.list_mine()


@router.post("/mcp-connections", response_model=McpConnectionView, status_code=201)
async def create_connection(
    payload: McpConnectionCreate,
    service: McpConnectionService = Depends(get_mcp_connection_service),
) -> McpConnectionView:
    """Register a new connection (409 on name collision with your own)."""
    return await service.create(payload)


@router.get("/mcp-connections/{name}", response_model=McpConnectionView)
async def get_connection(
    name: str,
    service: McpConnectionService = Depends(get_mcp_connection_service),
) -> McpConnectionView:
    """Get one connection by name."""
    return await service.get(name)


@router.patch("/mcp-connections/{name}", response_model=McpConnectionView)
async def update_connection(
    name: str,
    payload: McpConnectionUpdate,
    service: McpConnectionService = Depends(get_mcp_connection_service),
) -> McpConnectionView:
    """Patch url / headers / enabled (headers replace the whole set)."""
    return await service.update(name, payload)


@router.delete("/mcp-connections/{name}", status_code=204)
async def delete_connection(
    name: str,
    service: McpConnectionService = Depends(get_mcp_connection_service),
) -> Response:
    """Remove a connection."""
    await service.delete(name)
    return Response(status_code=204)
