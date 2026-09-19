"""Hub persistence: repository ports and SQLAlchemy implementations.

All hub SQL lives here. Services depend on the ports and get the SQLAlchemy
implementations by injection; tests inject fakes. ORM rows cross the port
boundary — mapping rows to API/domain models is the service's job (same
division as the framework's assistant_service).

WARNING: McpConnection rows carry decrypted credentials (``headers``) in
memory. Rows must never cross into response models or logs.
"""

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db import McpConnection as McpConnectionORM
from hub.db import Skill as SkillORM
from hub.db import SkillFile as SkillFileORM


class SkillRepository(Protocol):
    """Persistence port for user-tier skills. Methods commit their own unit
    of work; reads leave the session clean."""

    async def list_for_owner(self, owner_id: str) -> list[SkillORM]: ...
    async def list_by_names(self, names: frozenset[str]) -> list[SkillORM]: ...
    async def list_all(self) -> list[SkillORM]: ...
    async def get_for_owner(self, owner_id: str, name: str) -> SkillORM | None: ...
    async def list_files(self, skill_id: str) -> list[SkillFileORM]: ...

    async def save(
        self,
        owner_id: str,
        name: str,
        *,
        description: str,
        license: str | None,
        metadata: dict,
        files: dict[str, bytes],
    ) -> SkillORM:
        """Insert or fully replace the (owner, name) skill. Commits."""
        ...

    async def delete(self, row: SkillORM) -> None:
        """Delete the skill (files cascade). Commits."""
        ...


class SqlAlchemySkillRepository:
    """SkillRepository over the metadata DB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_owner(self, owner_id: str) -> list[SkillORM]:
        rows = await self._session.execute(select(SkillORM).where(SkillORM.user_id == owner_id).order_by(SkillORM.name))
        return list(rows.scalars().all())

    async def list_by_names(self, names: frozenset[str]) -> list[SkillORM]:
        rows = await self._session.execute(select(SkillORM).where(SkillORM.name.in_(names)).order_by(SkillORM.name))
        return list(rows.scalars().all())

    async def list_all(self) -> list[SkillORM]:
        rows = await self._session.execute(select(SkillORM).order_by(SkillORM.name))
        return list(rows.scalars().all())

    async def get_for_owner(self, owner_id: str, name: str) -> SkillORM | None:
        row = await self._session.execute(select(SkillORM).where(SkillORM.user_id == owner_id, SkillORM.name == name))
        return row.scalar_one_or_none()

    async def list_files(self, skill_id: str) -> list[SkillFileORM]:
        rows = await self._session.execute(
            select(SkillFileORM).where(SkillFileORM.skill_id == skill_id).order_by(SkillFileORM.path)
        )
        return list(rows.scalars().all())

    async def save(
        self,
        owner_id: str,
        name: str,
        *,
        description: str,
        license: str | None,
        metadata: dict,
        files: dict[str, bytes],
    ) -> SkillORM:
        row = await self.get_for_owner(owner_id, name)
        if row is None:
            row = SkillORM(
                user_id=owner_id, name=name, description=description, license=license, metadata_dict=metadata
            )
            self._session.add(row)
            await self._session.flush()  # assigns skill_id via server default
        else:
            await self._session.execute(delete(SkillFileORM).where(SkillFileORM.skill_id == row.skill_id))
            row.description = description
            row.license = license
            row.metadata_dict = metadata
            row.updated_at = datetime.now(tz=UTC)
        self._session.add_all(
            SkillFileORM(skill_id=row.skill_id, path=path, content=content) for path, content in files.items()
        )
        await self._session.commit()
        return row

    async def delete(self, row: SkillORM) -> None:
        await self._session.delete(row)
        await self._session.commit()


class McpConnectionRepository(Protocol):
    """Persistence port for user-tier MCP connections."""

    async def list_for_owner(self, owner_id: str) -> list[McpConnectionORM]: ...
    async def get_for_owner(self, owner_id: str, name: str) -> McpConnectionORM | None: ...

    async def insert(
        self, owner_id: str, name: str, *, transport: str, url: str, auth_type: str, headers: dict[str, str]
    ) -> McpConnectionORM:
        """Insert a new connection. Commits."""
        ...

    async def save(self, row: McpConnectionORM) -> None:
        """Persist mutations to an existing row. Commits."""
        ...

    async def delete(self, row: McpConnectionORM) -> None:
        """Delete the connection. Commits."""
        ...


class SqlAlchemyMcpConnectionRepository:
    """McpConnectionRepository over the metadata DB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_owner(self, owner_id: str) -> list[McpConnectionORM]:
        rows = await self._session.execute(
            select(McpConnectionORM).where(McpConnectionORM.user_id == owner_id).order_by(McpConnectionORM.name)
        )
        return list(rows.scalars().all())

    async def get_for_owner(self, owner_id: str, name: str) -> McpConnectionORM | None:
        row = await self._session.execute(
            select(McpConnectionORM).where(McpConnectionORM.user_id == owner_id, McpConnectionORM.name == name)
        )
        return row.scalar_one_or_none()

    async def insert(
        self, owner_id: str, name: str, *, transport: str, url: str, auth_type: str, headers: dict[str, str]
    ) -> McpConnectionORM:
        row = McpConnectionORM(
            user_id=owner_id,
            name=name,
            transport=transport,
            url=url,
            auth_type=auth_type,
            headers=headers,
            enabled=True,
        )
        self._session.add(row)
        await self._session.commit()
        return row

    async def save(self, row: McpConnectionORM) -> None:
        row.updated_at = datetime.now(tz=UTC)
        await self._session.commit()

    async def delete(self, row: McpConnectionORM) -> None:
        await self._session.delete(row)
        await self._session.commit()
