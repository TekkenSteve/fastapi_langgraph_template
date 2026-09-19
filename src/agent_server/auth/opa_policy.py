"""OPA (Open Policy Agent) binding for the policy engine port.

OPA is the mature externalized-policy service — this module is only the thin
HTTP adapter; no policy logic is reinvented here. The Rego document owns the
rules (ship ``deployments/opa/hub_authz.rego`` as the drop-in equivalent of
LocalPolicyEngine, extend it for org/sharing rules without code changes).

Document contract (package ``hub.authz`` by default):

- ``allow`` (bool) ← check/require. Input:
  ``{"subject": {"identity", "permissions"}, "permission": "read",
    "resource": {"type", "id", "owner_id"}}``
- ``filter`` (object) ← access_filter. Result shape mirrors
  ``AccessFilter``: ``{"allow_all": bool, "owner_id": str|null,
  "object_ids": [...]|null}``.

Failure posture is fail-closed by default: an unreachable OPA must not
silently authorize. Set OPA_FAIL_CLOSED=false to degrade to the local
semantics fallback instead (CompositePolicyEngine with LocalPolicyEngine is
the usual reason to want that).
"""

from typing import Any

import httpx
import structlog
from fastapi import HTTPException

from agent_server.auth.policy import LocalPolicyEngine, PolicyEngine
from agent_server.config.settings import settings
from agent_server.domain.policy import AccessFilter, DecisionContext, Permission, ResourceRef, ResourceType
from agent_server.domain.user import User

logger = structlog.getLogger(__name__)


class OpaPolicyEngine(PolicyEngine):
    """PolicyEngine that delegates decisions to an OPA sidecar."""

    def __init__(self, base_url: str, *, package: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._package = package.strip("/")
        self._fallback = LocalPolicyEngine()
        self._client = httpx.AsyncClient(timeout=settings.policy.OPA_TIMEOUT_SECS, transport=transport)

    async def _query(self, rule: str, input_doc: dict[str, Any]) -> Any:
        """POST /v1/data/<package>/<rule>; raises OpaUnavailable on failure."""
        url = f"{self._base_url}/v1/data/{self._package}/{rule}"
        try:
            response = await self._client.post(url, json={"input": input_doc})
            response.raise_for_status()
            return response.json().get("result")
        except (httpx.HTTPError, ValueError) as e:
            raise OpaUnavailable(str(e)) from e

    def _input(
        self, subject: User, permission: Permission, resource: ResourceRef, context: DecisionContext | None
    ) -> dict[str, Any]:
        return {
            "subject": {"identity": subject.identity, "permissions": subject.permissions or []},
            "permission": str(permission),
            "resource": {"type": str(resource.type), "id": resource.id, "owner_id": resource.owner_id},
            "context": context or {},
        }

    async def _decide(self, rule: str, input_doc: dict[str, Any]) -> Any:
        """Query OPA, or fall back / fail closed per OPA_FAIL_CLOSED."""
        try:
            return await self._query(rule, input_doc)
        except OpaUnavailable as e:
            logger.error("opa_unavailable", rule=rule, error=str(e))
            if settings.policy.OPA_FAIL_CLOSED:
                raise HTTPException(status_code=503, detail="Policy service unavailable") from e
            return _FALLBACK

    async def check(
        self, subject: User, permission: Permission, resource: ResourceRef, context: DecisionContext | None = None
    ) -> bool:
        """Point decision (the OPA round trip)."""
        result = await self._decide("allow", self._input(subject, permission, resource, context))
        if result is _FALLBACK:
            return await self._fallback.check(subject, permission, resource)
        return bool(result)

    async def require(
        self, subject: User, permission: Permission, resource: ResourceRef, context: DecisionContext | None = None
    ) -> None:
        """403 on deny."""
        if not await self.check(subject, permission, resource, context):
            raise HTTPException(status_code=403, detail=f"Not authorized to {permission} this {resource.type}")

    async def access_filter(
        self, subject: User, permission: Permission, resource_type: ResourceType, context: DecisionContext | None = None
    ) -> AccessFilter:
        """List/search narrowing (the ListObjects-shaped query)."""
        input_doc = {
            "subject": {"identity": subject.identity, "permissions": subject.permissions or []},
            "permission": str(permission),
            "resource": {"type": str(resource_type)},
            "context": context or {},
        }
        result = await self._decide("filter", input_doc)
        if result is _FALLBACK:
            return await self._fallback.access_filter(subject, permission, resource_type)
        return AccessFilter(
            allow_all=bool(result.get("allow_all")),
            owner_id=result.get("owner_id"),
            object_ids=frozenset(result["object_ids"]) if result.get("object_ids") else None,
        )


class OpaUnavailable(Exception):
    """The OPA sidecar could not be reached or answered garbage."""


_FALLBACK = object()
