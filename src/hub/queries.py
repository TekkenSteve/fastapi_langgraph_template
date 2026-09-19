"""Graph-side loaders: read hub data straight from the ORM.

These run inside graph middleware / the MCP loader, outside FastAPI DI, so
they open their own sessions via the framework's ``get_session_maker``.
Graphs import these directly (same pattern as importing ``shop.backends``).
"""

from sqlalchemy import select

from agent_server.repo.orm import get_session_maker
from hub.db import McpConnection as McpConnectionORM
from hub.db import SkillFile as SkillFileORM
from hub.oauth.flow import build_oauth_auth
from hub.repositories import SqlAlchemySkillRepository


async def load_user_skill_contents(user_id: str) -> list[dict]:
    """Graph-side loader: every skill of ``user_id`` as {name, files: {path: text}}.

    Opens its own session — called from graph middleware, outside FastAPI DI.
    Returns plain dicts so graphs stay decoupled from the domain models.
    """
    maker = get_session_maker()
    async with maker() as session:
        repo = SqlAlchemySkillRepository(session)
        skills = await repo.list_for_owner(user_id)
        if not skills:
            return []
        rows = await session.execute(
            select(SkillFileORM).where(SkillFileORM.skill_id.in_([s.skill_id for s in skills]))
        )
        files = rows.scalars().all()
    by_skill: dict[str, dict[str, str]] = {}
    for f in files:
        by_skill.setdefault(f.skill_id, {})[f.path] = f.content.decode("utf-8")
    return [{"name": s.name, "files": by_skill.get(s.skill_id, {})} for s in skills]


async def load_enabled_connection_map(user_id: str) -> dict[str, dict]:
    """Graph-side loader: enabled connections as a langchain-mcp-adapters map.

    Opens its own session — called from the MCP loader, outside FastAPI DI.
    Includes credentials (headers): this map feeds the MCP client directly
    and must never be serialized into logs or responses.
    """
    maker = get_session_maker()
    async with maker() as session:
        rows = await session.execute(
            select(McpConnectionORM).where(McpConnectionORM.user_id == user_id, McpConnectionORM.enabled.is_(True))
        )
        connections = rows.scalars().all()
    result: dict[str, dict] = {}
    for r in connections:
        conn: dict = {"transport": r.transport, "url": r.url}
        if r.auth_type == "oauth":
            # Per-user SDK auth provider (PKCE/DCR/refresh); the first call
            # pauses the run with a connect_url interrupt until authorized.
            conn["auth"] = build_oauth_auth(r.user_id, r.name, r.url)
        elif r.auth_type == "headers" and r.headers:
            conn["headers"] = r.headers
        result[r.name] = conn
    return result
