"""Hub tables (business schema, own migration chain).

These are application-layer tables — they live in the hub package, not in
the framework ORM, and migrate through ``src/hub/migrations`` (separate
alembic chain, ``alembic_version_hub``), per the repo rule that business
tables never mix into the framework chain.

Sessions come from the framework's shared engine (``get_session_maker``) —
separate declarative Base, same database.
"""

from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Index, LargeBinary, Text, text
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from agent_server.repo.orm import EncryptedJson, JsonbSafe

Base = declarative_base()


class Skill(Base):
    """A user-tier skill installed through the hub (see models.py).

    Builtin skills ship inside graph packages and never touch this table.
    """

    __tablename__ = "skill"

    skill_id: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("gen_random_uuid()::text"))
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    license: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_dict: Mapped[dict] = mapped_column(JsonbSafe, server_default=text("'{}'::jsonb"), name="metadata")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))

    __table_args__ = (
        Index("idx_skill_user", "user_id"),
        Index("idx_skill_user_name", "user_id", "name", unique=True),
    )


class SkillFile(Base):
    """One file of a user-tier skill. Content is bytea so binary assets can
    be supported later without a schema change; the API currently accepts
    UTF-8 text only (see models.py)."""

    __tablename__ = "skill_file"

    skill_file_id: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("gen_random_uuid()::text"))
    skill_id: Mapped[str] = mapped_column(Text, ForeignKey("skill.skill_id", ondelete="CASCADE"), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))

    __table_args__ = (
        Index("idx_skill_file_skill", "skill_id"),
        Index("idx_skill_file_skill_path", "skill_id", "path", unique=True),
    )


class McpConnection(Base):
    """A user-tier MCP connection (streamable_http only; see models.py).

    ``headers`` holds credentials — Fernet-encrypted at rest (EncryptedJson),
    write-only at the API layer. Never serialize it into a response model.
    """

    __tablename__ = "mcp_connection"

    mcp_connection_id: Mapped[str] = mapped_column(
        Text, primary_key=True, server_default=text("gen_random_uuid()::text")
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'streamable_http'"))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # none = no credentials, headers = static headers, oauth = SDK-driven flow
    auth_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'none'"))
    headers: Mapped[dict | None] = mapped_column(EncryptedJson, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))

    __table_args__ = (
        Index("idx_mcp_connection_user", "user_id"),
        Index("idx_mcp_connection_user_name", "user_id", "name", unique=True),
    )


class McpOauthClient(Base):
    """Durable DCR client registration per (user, connection) — the OAuth
    counterpart of mcp_connection: tokens are ephemeral (TokenStore), client
    registrations are not. ``registration`` is EncryptedJson (client_secret).
    """

    __tablename__ = "mcp_oauth_client"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    connection_name: Mapped[str] = mapped_column(Text, primary_key=True)
    registration: Mapped[dict | None] = mapped_column(EncryptedJson, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
