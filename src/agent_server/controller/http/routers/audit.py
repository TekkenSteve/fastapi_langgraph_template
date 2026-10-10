"""Audit ledger query surface (admin only).

The ledger records what agents did; reading it is an operator action, not a user
one — a non-admin must not be able to enumerate other tenants' tool calls. That
is expressed through the policy engine rather than a role check here: the
resource is deliberately **ownerless**, and :class:`LocalPolicyEngine` denies
ownerless resources, so the default deployment is admin-only and an external
engine (OPA/OpenFGA) can widen or narrow it without this file changing.

Registered in the protocol app with an ``EXEMPT_PATHS`` entry because it
authorizes through the policy engine, not ``@auth.on`` dispatch — the same
arrangement the application-layer surfaces use.
"""

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_server.auth.deps import auth_dependency, get_current_user
from agent_server.auth.policy import PolicyEngine, get_policy_engine
from agent_server.domain.policy import READ, ResourceRef, ResourceType
from agent_server.domain.user import User
from agent_server.repo.audit import list_audit_entries
from agent_server.repo.orm import get_session

logger = structlog.getLogger(__name__)

# The hub declares its own policy families; so does the ledger.
AUDIT_LOG = ResourceType("audit_log")

router = APIRouter(prefix="/audit", tags=["Audit"], dependencies=auth_dependency)


class AuditEntry(BaseModel):
    """One ledger row. ``detail`` is a short summary, never a payload."""

    entry_id: int
    user_id: str | None
    thread_id: str | None
    run_id: str | None
    action: str
    resource: str | None
    status: str
    duration_ms: int | None
    detail: dict[str, Any] | None
    created_at: datetime


@router.get("/logs", response_model=list[AuditEntry])
async def list_logs(
    user_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    action: str | None = None,
    before: int | None = Query(None, description="Return entries older than this entry_id (paging cursor)"),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy_engine),
    user: User = Depends(get_current_user),
) -> list[AuditEntry]:
    """Read the audit ledger, newest first."""
    await policy.require(user, READ, ResourceRef(AUDIT_LOG, "all"))
    rows = await list_audit_entries(
        session,
        user_id=user_id,
        thread_id=thread_id,
        run_id=run_id,
        action=action,
        before=before,
        limit=limit,
    )
    return [AuditEntry.model_validate(row, from_attributes=True) for row in rows]
