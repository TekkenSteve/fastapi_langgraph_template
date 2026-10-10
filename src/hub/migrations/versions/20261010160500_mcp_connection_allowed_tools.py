"""MCP connection tool allowlist.

Revision ID: b2d4e6f8a1c3
Revises: 49b196a7ba6d
Create Date: 2026-10-10 16:05:00.000000

Hand-written: a single nullable column, no data migration.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text

import agent_server.repo.orm

# revision identifiers, used by Alembic.
revision = "b2d4e6f8a1c3"
down_revision = "49b196a7ba6d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_connection",
        sa.Column("allowed_tools", agent_server.repo.orm.JsonbSafe(astext_type=Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_connection", "allowed_tools")
