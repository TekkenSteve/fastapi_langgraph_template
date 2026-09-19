"""oauth: auth_type + mcp_oauth_client

Revision ID: 49b196a7ba6d
Revises: 7639c0070845
Create Date: 2026-09-18 20:17:54.683270

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "49b196a7ba6d"
down_revision = "7639c0070845"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_connection",
        sa.Column("auth_type", sa.Text(), server_default=sa.text("'none'"), nullable=False),
    )
    op.create_table(
        "mcp_oauth_client",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("connection_name", sa.Text(), nullable=False),
        # EncryptedJson at the ORM layer; a plain Text column here.
        sa.Column("registration", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "connection_name"),
    )


def downgrade() -> None:
    op.drop_table("mcp_oauth_client")
    op.drop_column("mcp_connection", "auth_type")
