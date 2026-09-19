"""Per-invocation authorization for MCP tool calls (PolicyToolInterceptor; lives in auth/ with the other engines).

Level-4 agent maturity is per-invocation authorization — not checked once at
connection time, but on every tool call. This interceptor plugs into the
langchain-mcp-adapters ToolCallInterceptor chain (load_mcp_tools composes it
when MCP_TOOL_AUTHZ_ENABLED=true) and funnels every call through the policy
engine: ``check(user, EXECUTE, mcp_tool:<server>/<tool>)``.

Fail-closed by construction: the interceptor's subject carries only an
identity (load-time knows no permissions), and LocalPolicyEngine denies
ownerless resources — so enabling this without a real policy backend
(OPA/Cedar/OpenFGA) blocks all tool calls. Deliberate: the switch is meant
to be flipped together with a policy backend.
"""

from typing import Any

import structlog

from agent_server.auth.policy import PolicyEngine
from agent_server.domain.policy import Permission, ResourceRef, ResourceType
from agent_server.domain.user import User

logger = structlog.getLogger(__name__)

EXECUTE = Permission("execute")
MCP_TOOL = ResourceType("mcp_tool")


class PolicyToolInterceptor:
    """Authorizes every MCP tool call through the policy engine."""

    def __init__(self, policy: PolicyEngine, user_id: str | None) -> None:
        self._policy = policy
        self._user_id = user_id

    async def __call__(self, request: Any, handler: Any) -> Any:
        """Deny raises (HTTPException 403/503); allow falls through to the handler."""
        user = User(identity=self._user_id or "system")
        resource = ResourceRef(MCP_TOOL, f"{request.server_name}/{request.name}")
        await self._policy.require(user, EXECUTE, resource)
        logger.debug("mcp_tool_authorized", user=user.identity, tool=resource.id)
        return await handler(request)
