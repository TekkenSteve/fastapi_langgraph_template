"""POST /threads/prune by id against a real PostgreSQL.

The claim is `FOR UPDATE ... SKIP LOCKED` over the thread table with an
active-run guard and a user filter — properties no session fake can show. These
tests drive the real statement; they skip when no database is reachable or the
framework tables are missing (run `make migrate-up` first).
"""

import uuid
from collections.abc import AsyncIterator, Callable

import asyncpg
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_server.config.settings import settings
from agent_server.repo import database
from agent_server.repo.orm import Run as RunORM
from agent_server.repo.orm import Thread as ThreadORM
from agent_server.usecase.thread_ttl import prune_threads_by_id

_OWNER = "prune-by-id-test-owner"
_OTHER = "prune-by-id-test-other"

SessionFactory = Callable[[], AsyncSession]


@pytest.fixture
async def sessions() -> AsyncIterator[SessionFactory]:
    """Sessions on independent connections, or skip without a database."""
    engine = create_async_engine(settings.db.database_url)
    try:
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
                if await conn.scalar(text("SELECT to_regclass('public.thread')")) is None:
                    pytest.skip("thread table is unavailable; run Alembic migrations before this DB test")
        except (OperationalError, OSError, asyncpg.PostgresError) as exc:
            pytest.skip(f"PostgreSQL test database is unavailable: {exc}")

        initialized_here = database.db_manager.engine is None
        if initialized_here:
            await database.db_manager.initialize()
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            yield maker
        finally:
            async with maker() as cleanup:
                await cleanup.execute(delete(ThreadORM).where(ThreadORM.user_id.in_((_OWNER, _OTHER))))
                await cleanup.commit()
            if initialized_here:
                await database.db_manager.close()
    finally:
        await engine.dispose()


async def _create_thread(sessions: SessionFactory, user_id: str, *, status: str = "idle") -> str:
    thread_id = f"prune-{uuid.uuid4()}"
    async with sessions() as session:
        session.add(ThreadORM(thread_id=thread_id, status=status, metadata_json={}, user_id=user_id))
        await session.commit()
    return thread_id


async def _add_active_run(sessions: SessionFactory, thread_id: str) -> None:
    async with sessions() as session:
        session.add(RunORM(run_id=f"run-{uuid.uuid4()}", thread_id=thread_id, status="pending", user_id=_OWNER))
        await session.commit()


async def _exists(sessions: SessionFactory, thread_id: str) -> bool:
    async with sessions() as session:
        return await session.scalar(select(ThreadORM.thread_id).where(ThreadORM.thread_id == thread_id)) is not None


@pytest.mark.asyncio
async def test_delete_prunes_only_the_callers_idle_threads(sessions: SessionFactory) -> None:
    """Other users' threads and threads with active runs are left alone."""
    mine = await _create_thread(sessions, _OWNER)
    busy = await _create_thread(sessions, _OWNER)
    await _add_active_run(sessions, busy)
    foreign = await _create_thread(sessions, _OTHER)

    async with sessions() as session:
        count = await prune_threads_by_id(session, thread_ids=[mine, busy, foreign], strategy="delete", user_id=_OWNER)

    assert count == 1
    assert not await _exists(sessions, mine)
    assert await _exists(sessions, busy)
    assert await _exists(sessions, foreign)


@pytest.mark.asyncio
async def test_keep_latest_keeps_the_thread_row(sessions: SessionFactory) -> None:
    """Compaction keeps the thread; only its history would shrink."""
    thread_id = await _create_thread(sessions, _OWNER)

    async with sessions() as session:
        count = await prune_threads_by_id(session, thread_ids=[thread_id], strategy="keep_latest", user_id=_OWNER)

    assert count == 1
    assert await _exists(sessions, thread_id)


@pytest.mark.asyncio
async def test_unknown_ids_are_not_counted(sessions: SessionFactory) -> None:
    async with sessions() as session:
        count = await prune_threads_by_id(session, thread_ids=["does-not-exist"], strategy="delete", user_id=_OWNER)

    assert count == 0


@pytest.mark.asyncio
async def test_auth_filter_narrows_the_claim(sessions: SessionFactory) -> None:
    """A metadata policy must be able to exclude a thread the user owns."""
    tagged = await _create_thread(sessions, _OWNER)
    async with sessions() as session:
        await session.execute(
            text('UPDATE thread SET metadata_json = \'{"team": "alpha"}\'::jsonb WHERE thread_id = :tid'),
            {"tid": tagged},
        )
        await session.commit()

    async with sessions() as session:
        count = await prune_threads_by_id(
            session,
            thread_ids=[tagged],
            strategy="delete",
            user_id=_OWNER,
            auth_filter=ThreadORM.metadata_json["team"].astext != "alpha",
        )

    assert count == 0
    assert await _exists(sessions, tagged)
