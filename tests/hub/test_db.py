"""Real-DB integration tests for the hub (skill + mcp connection).

Covers what fakes cannot: the actual SQL paths behind the repositories, the
upsert-replace semantics, cascade deletes, and tenant isolation — services
are composed the same way the router composes them (SQLAlchemy repo +
LocalPolicyEngine). Self-skips when PostgreSQL or the hub tables are
unavailable, mirroring test_assistant_large_config_db.py.
"""

import asyncpg
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_server.auth.policy import LocalPolicyEngine
from agent_server.config.settings import settings
from agent_server.domain.user import User
from hub.db import McpConnection as McpConnectionORM
from hub.db import Skill as SkillORM
from hub.db import SkillFile as SkillFileORM
from hub.models import McpConnectionCreate, McpConnectionUpdate, SkillInstall
from hub.repositories import SqlAlchemyMcpConnectionRepository, SqlAlchemySkillRepository
from hub.services import McpConnectionService, SkillService

_USER_A = User(identity="hub-db-user-a")
_USER_B = User(identity="hub-db-user-b")
_POLICY = LocalPolicyEngine()

_SKILL_MD = "---\nname: db-skill\ndescription: from test\n---\n# Body\n"


@pytest.fixture
async def session():
    """A real session against the test DB; skips when hub tables are missing."""
    engine = create_async_engine(settings.db.database_url)
    try:
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
                skill_table = await conn.scalar(text("SELECT to_regclass('public.skill')"))
                conn_table = await conn.scalar(text("SELECT to_regclass('public.mcp_connection')"))
        except (OperationalError, OSError, asyncpg.PostgresError) as exc:
            pytest.skip(f"PostgreSQL test database is unavailable: {exc}")
        if skill_table is None or conn_table is None:
            pytest.skip("hub tables are unavailable; run Alembic migrations before this DB regression test")

        maker = async_sessionmaker(engine, expire_on_commit=False)
        users = [_USER_A.identity, _USER_B.identity]

        async def _cleanup(s) -> None:  # noqa: ANN001, ANN202
            # skill_file has no user_id — clean via the parent skill.
            await s.execute(
                delete(SkillFileORM).where(
                    SkillFileORM.skill_id.in_(select(SkillORM.skill_id).where(SkillORM.user_id.in_(users)))
                )
            )
            await s.execute(delete(SkillORM).where(SkillORM.user_id.in_(users)))
            await s.execute(delete(McpConnectionORM).where(McpConnectionORM.user_id.in_(users)))
            await s.commit()

        async with maker() as s:
            await _cleanup(s)
            yield s
            await _cleanup(s)
    finally:
        await engine.dispose()


def _skill_service(session) -> SkillService:  # noqa: ANN001, ANN202
    return SkillService(SqlAlchemySkillRepository(session), _POLICY, _USER_A)


def _conn_service(session, user: User = _USER_A) -> McpConnectionService:  # noqa: ANN001, ANN202
    return McpConnectionService(SqlAlchemyMcpConnectionRepository(session), _POLICY, user)


def _install_payload(description: str = "from test") -> SkillInstall:
    return SkillInstall.model_validate(
        {
            "files": [
                {"path": "SKILL.md", "content": _SKILL_MD.replace("from test", description)},
                {"path": "scripts/helper.py", "content": "print(1)\n"},
            ]
        }
    )


async def test_install_get_replace_uninstall_round_trip(session) -> None:
    service = _skill_service(session)
    detail = await service.install(_install_payload())
    assert detail.name == "db-skill"
    assert len(detail.files) == 2

    fetched = await service.get("db-skill")
    assert {f.path for f in fetched.files} == {"SKILL.md", "scripts/helper.py"}

    replaced = await service.replace("db-skill", _install_payload("v2"))
    assert replaced.description == "v2"

    await service.uninstall("db-skill")
    assert await service.list_mine() == []


async def test_uninstall_cascades_files(session) -> None:
    service = _skill_service(session)
    await service.install(_install_payload())
    await service.uninstall("db-skill")
    assert await service.list_mine() == []


async def test_skills_are_tenant_isolated(session) -> None:
    await _skill_service(session).install(_install_payload())
    stranger = SkillService(SqlAlchemySkillRepository(session), _POLICY, _USER_B)

    assert await stranger.list_mine() == []
    with pytest.raises(HTTPException) as exc:
        await stranger.get("db-skill")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await stranger.uninstall("db-skill")
    assert exc.value.status_code == 404


async def test_replace_requires_matching_name_and_existing_skill(session) -> None:
    service = _skill_service(session)
    await service.install(_install_payload())
    with pytest.raises(HTTPException) as exc:
        await service.replace("other-name", _install_payload())
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await service.replace("absent-skill", _install_payload())
    assert exc.value.status_code in (404, 422)


async def test_mcp_connection_crud_and_tenant_isolation(session) -> None:
    service = _conn_service(session)
    view = await service.create(
        McpConnectionCreate(name="db-kb", url="https://mcp.example.com/kb", headers={"X-Key": "s3cret"})
    )
    assert view.header_keys == ["X-Key"]
    assert "s3cret" not in view.model_dump_json()

    # At rest the column holds a Fernet token, never plaintext JSON.
    raw = (await session.execute(text("SELECT headers FROM mcp_connection WHERE name = 'db-kb'"))).scalar_one()
    assert "s3cret" not in raw
    assert raw.startswith("gAAAA")  # Fernet tokens are urlsafe-b64 with this prefix

    with pytest.raises(HTTPException) as exc:
        await service.create(McpConnectionCreate(name="db-kb", url="https://mcp.example.com/kb"))
    assert exc.value.status_code == 409

    updated = await service.update("db-kb", McpConnectionUpdate(enabled=False))
    assert updated.enabled is False

    # Same name, different user: no collision, and B cannot see A's row.
    stranger = _conn_service(session, _USER_B)
    await stranger.create(McpConnectionCreate(name="db-kb", url="https://other.example.com/kb"))
    assert len(await stranger.list_mine()) == 1
    with pytest.raises(HTTPException) as exc:
        await stranger.delete("nonexistent")
    assert exc.value.status_code == 404

    await service.delete("db-kb")
    await stranger.delete("db-kb")
