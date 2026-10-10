"""Audit ledger persistence: one writer, one reader.

The ledger is a framework table written best-effort by server middleware and
read by the admin query surface. Two properties matter more than features:

- **a failed write never fails a run.** The audit trail is secondary to the work
  it describes; a missing row is worse than a broken run only in theory. The
  writer swallows (and logs) storage errors.
- **it opens its own session.** The writer runs inside a graph node, outside
  FastAPI's dependency injection, exactly like ``hub/queries.py`` does.

Events cross this boundary as plain dicts: the middleware that produces them is
graph-side code, which must not import the server's ORM models.
"""

from typing import Any

import structlog

from agent_server.repo.orm import get_session_maker

logger = structlog.getLogger(__name__)

# Bounds on what a single event may store. The ledger is for "who did what,
# when, and how it went" — not for payload archival.
_MAX_RESOURCE_CHARS = 200
_MAX_DETAIL_CHARS = 2000


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


async def record_audit_event(event: dict[str, Any]) -> None:
    """Append one event to the ledger. Never raises.

    Recognized keys: ``user_id``, ``thread_id``, ``run_id``, ``action`` (required
    — a row without one answers nothing), ``resource``, ``status``, ``duration_ms``,
    ``detail``.
    """
    action = _clip(event.get("action"), _MAX_RESOURCE_CHARS)
    if not action:
        logger.warning("audit_event_without_action", keys=sorted(event))
        return

    from agent_server.repo.orm import AuditLog

    row = AuditLog(
        user_id=_clip(event.get("user_id"), _MAX_RESOURCE_CHARS),
        thread_id=_clip(event.get("thread_id"), _MAX_RESOURCE_CHARS),
        run_id=_clip(event.get("run_id"), _MAX_RESOURCE_CHARS),
        action=action,
        resource=_clip(event.get("resource"), _MAX_RESOURCE_CHARS),
        status=_clip(event.get("status"), 32) or "ok",
        duration_ms=event.get("duration_ms") if isinstance(event.get("duration_ms"), int) else None,
        detail={"message": _clip(event.get("detail"), _MAX_DETAIL_CHARS)} if event.get("detail") else None,
    )
    try:
        maker = get_session_maker()
        async with maker() as session:
            session.add(row)
            await session.commit()
    except Exception as e:  # a ledger write must never fail the run it describes
        logger.warning("audit_event_not_recorded", action=action, error=str(e))


async def list_audit_entries(
    session: Any,
    *,
    user_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    action: str | None = None,
    before: int | None = None,
    limit: int = 50,
) -> list[Any]:
    """Newest-first page of ledger entries, optionally filtered.

    ``before`` is the ``entry_id`` cursor: the caller passes the last id it saw,
    which keeps paging stable while new rows arrive (offset paging would skip or
    repeat rows under concurrent writes).
    """
    from sqlalchemy import select

    from agent_server.repo.orm import AuditLog

    stmt = select(AuditLog).order_by(AuditLog.entry_id.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if thread_id is not None:
        stmt = stmt.where(AuditLog.thread_id == thread_id)
    if run_id is not None:
        stmt = stmt.where(AuditLog.run_id == run_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    if before is not None:
        stmt = stmt.where(AuditLog.entry_id < before)
    rows = await session.execute(stmt)
    return list(rows.scalars().all())
