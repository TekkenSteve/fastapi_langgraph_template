"""Hub REST API: user-tier skills and MCP connections.

Application-layer surface, merged into the server through ``http.app`` in
langgraph.json (same path as shop/ml) — it is not part of the Agent Protocol
and deliberately does not touch the protocol server's router table or auth
registry. Authentication comes from the framework's auth_dependency;
authorization from the policy engine inside the services.
"""

import structlog
from fastapi import APIRouter, Depends, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agent_server.auth.deps import auth_dependency, get_current_user
from agent_server.auth.policy import PolicyEngine, get_policy_engine
from agent_server.auth.rate_limit import rate_limit_default
from agent_server.domain.user import User
from agent_server.repo.orm import get_session
from hub.models import (
    McpConnectionCreate,
    McpConnectionUpdate,
    McpConnectionView,
    SkillDetail,
    SkillImportRequest,
    SkillInstall,
    SkillSummary,
)
from hub.oauth.flow import abandon_from_browser, complete_from_browser
from hub.repositories import SqlAlchemyMcpConnectionRepository, SqlAlchemySkillRepository
from hub.services import McpConnectionService, SkillService

logger = structlog.getLogger(__name__)

# The default tier covers every hub route: skill import and the MCP Apps host
# proxy both do outbound network work, so they need the same abuse ceiling as
# the protocol routers (settings.app.RATE_LIMIT_DEFAULT). The public OAuth
# callback stays unauthenticated and un-limited by design.
router = APIRouter(tags=["hub"], dependencies=[*auth_dependency, Depends(rate_limit_default)])

# The OAuth callback is a browser redirect from the external auth server — it
# carries no user credentials, only the flow's state/code. It stays public and
# is protected by the unguessable state instead (single-use, short TTL).
public_router = APIRouter(tags=["hub"])


def _callback_page(title: str, detail: str) -> str:
    """A minimal page a human lands on. Never interpolates raw query text."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head>"
        "<body style='font-family: system-ui; max-width: 40rem; margin: 4rem auto'>"
        f"<h1>{title}</h1><p>{detail}</p></body></html>"
    )


@public_router.get("/hub/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    state: str = "",
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> HTMLResponse:
    """Finish (or abandon) a browser-delivered OAuth flow.

    The auth server redirects here with either ``code`` or ``error``. Both
    land on a human-readable page: a denial is a normal outcome, not a
    protocol error, and the caller sitting in the paused run must not be the
    only one who can see what happened.
    """
    if error is not None:
        await abandon_from_browser(state)
        # `error_description` is attacker-influenced text: it goes to the log
        # (truncated) for diagnosis, never into the page.
        logger.info("mcp_oauth_callback_error", error=error, description=(error_description or "")[:200])
        return HTMLResponse(
            _callback_page("Authorization failed", "The connection was not authorized. You can close this tab."),
            status_code=400,
        )
    if not code:
        return HTMLResponse(
            _callback_page("Authorization failed", "This link is missing its authorization code."),
            status_code=400,
        )
    if not await complete_from_browser(state, code):
        return HTMLResponse(
            _callback_page("Link expired", "This authorization link is unknown or has expired. Please retry."),
            status_code=400,
        )
    return HTMLResponse(_callback_page("Authorization complete", "You can close this tab and return to the agent."))


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
    service: McpConnectionService = Depends(get_mcp_connection_service),
) -> list[dict]:
    """List a connection's tools with metadata (visibility, ui)."""
    return await service.proxy_list_tools(name)


@router.post("/hub/mcp/{name}/tools/call")
async def mcp_tools_call(
    name: str,
    payload: ToolCallRequest,
    service: McpConnectionService = Depends(get_mcp_connection_service),
) -> dict:
    """Call one tool by name through the caller's connection."""
    return await service.proxy_call_tool(name, payload.tool_name, payload.args)


@router.post("/hub/mcp/{name}/resources/list")
async def mcp_resources_list(
    name: str,
    service: McpConnectionService = Depends(get_mcp_connection_service),
) -> dict:
    """List a connection's resources and resource templates."""
    return await service.proxy_list_resources(name)


class ResourceReadRequest(BaseModel):
    """Payload for the resources/read proxy."""

    uri: str


@router.post("/hub/mcp/{name}/resources/read")
async def mcp_resources_read(
    name: str,
    payload: ResourceReadRequest,
    service: McpConnectionService = Depends(get_mcp_connection_service),
) -> dict:
    """Read one resource by URI (text contents only)."""
    return await service.proxy_read_resource(name, payload.uri)


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
