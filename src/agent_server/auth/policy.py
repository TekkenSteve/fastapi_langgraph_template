"""Policy engine port and shipped implementations.

Every hub authorization decision funnels through ``PolicyEngine`` — services
receive an engine by constructor injection (never a global lookup), routers
receive one via the ``get_policy_engine`` FastAPI provider.

Shipped engines:
- ``LocalPolicyEngine`` — default semantics: owner or ``admin`` permission;
  list narrowing by ownership. Zero dependencies, works out of the box.
- ``CompositePolicyEngine`` — AND-composition: every engine must allow.
  The extension point for additive rules (quota, domain restriction,
  approval gates) without modifying existing engines.

Binding a decision service (OpenFGA/SpiceDB) means implementing the same
Protocol — ``check`` → Check API, ``access_filter`` → ListObjects — and
installing it with ``configure_policy_engine()`` during lifespan. Hub code
does not change. See docs/design/hub.md.
"""

import asyncio
from collections.abc import Sequence

from fastapi import HTTPException

from agent_server.domain.policy import AccessFilter, DecisionContext, Permission, ResourceRef, ResourceType
from agent_server.domain.user import User

ADMIN_PERMISSION = "admin"


class PolicyEngine:
    """Authorization decisions for hub resources (Protocol-by-convention).

    Not ``typing.Protocol``: engines are composed and stored, and an explicit
    base class keeps ``isinstance`` checks and subclassing honest. Implement
    all three methods. They are async because real decision backends are I/O
    (an OPA sidecar today, OpenFGA/SpiceDB tomorrow) — local engines simply
    return immediately.
    """

    async def check(
        self,
        subject: User,
        permission: Permission,
        resource: ResourceRef,
        context: DecisionContext | None = None,
    ) -> bool:
        """Whether ``subject`` may perform ``permission`` on ``resource``."""
        raise NotImplementedError

    async def require(
        self,
        subject: User,
        permission: Permission,
        resource: ResourceRef,
        context: DecisionContext | None = None,
    ) -> None:
        """Raise 403 unless :meth:`check` passes."""
        raise NotImplementedError

    async def access_filter(
        self,
        subject: User,
        permission: Permission,
        resource_type: ResourceType,
        context: DecisionContext | None = None,
    ) -> AccessFilter:
        """List/search narrowing for ``subject`` over ``resource_type``."""
        raise NotImplementedError


def _is_admin(subject: User) -> bool:
    return ADMIN_PERMISSION in (subject.permissions or [])


class LocalPolicyEngine(PolicyEngine):
    """Default policy: the owner may do anything; ``admin`` permission may too."""

    async def check(
        self, subject: User, permission: Permission, resource: ResourceRef, context: DecisionContext | None = None
    ) -> bool:  # noqa: ARG002
        return subject.identity == resource.owner_id or _is_admin(subject)

    async def require(
        self, subject: User, permission: Permission, resource: ResourceRef, context: DecisionContext | None = None
    ) -> None:
        if not await self.check(subject, permission, resource, context):
            raise HTTPException(status_code=403, detail=f"Not authorized to {permission} this {resource.type}")

    async def access_filter(
        self, subject: User, permission: Permission, resource_type: ResourceType, context: DecisionContext | None = None
    ) -> AccessFilter:  # noqa: ARG002
        if _is_admin(subject):
            return AccessFilter(allow_all=True)
        return AccessFilter(owner_id=subject.identity)


class CompositePolicyEngine(PolicyEngine):
    """AND-composes engines: any deny denies.

    Additive rules (quota, domain restrictions, approval gates) are new
    entries in ``engines``, never edits to existing ones.
    """

    def __init__(self, engines: Sequence[PolicyEngine]) -> None:
        if not engines:
            raise ValueError("CompositePolicyEngine needs at least one engine")
        self._engines = list(engines)

    async def check(
        self, subject: User, permission: Permission, resource: ResourceRef, context: DecisionContext | None = None
    ) -> bool:
        checks = [e.check(subject, permission, resource, context) for e in self._engines]
        return all(await asyncio.gather(*checks))

    async def require(
        self, subject: User, permission: Permission, resource: ResourceRef, context: DecisionContext | None = None
    ) -> None:
        for engine in self._engines:
            await engine.require(subject, permission, resource, context)

    async def access_filter(
        self, subject: User, permission: Permission, resource_type: ResourceType, context: DecisionContext | None = None
    ) -> AccessFilter:
        """Most-restrictive composition: identical owner filters pass through,
        object-id sets intersect, disagreement narrows to nothing."""
        filters = await asyncio.gather(
            *[e.access_filter(subject, permission, resource_type, context) for e in self._engines]
        )
        if all(f.allow_all for f in filters):
            return AccessFilter(allow_all=True)
        id_sets = [f.object_ids for f in filters if f.object_ids is not None]
        if id_sets:
            return AccessFilter(object_ids=frozenset.intersection(*id_sets))
        owners = {f.owner_id for f in filters if f.owner_id is not None}
        if len(owners) == 1:
            return AccessFilter(owner_id=owners.pop())
        return AccessFilter(object_ids=frozenset())


_engine: PolicyEngine = LocalPolicyEngine()


def configure_policy_engine(engine: PolicyEngine) -> None:
    """Install the process-wide engine. Call once during lifespan, before serving."""
    global _engine
    _engine = engine


def get_policy_engine() -> PolicyEngine:
    """FastAPI provider: the configured engine (``LocalPolicyEngine`` default)."""
    return _engine
