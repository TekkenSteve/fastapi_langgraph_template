"""Framework schema (squashed initial migration).

Revision ID: 51dcc8f487fb
Revises:
Create Date: 2026-09-05 20:06:46.277047

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text

import agent_server.repo.orm

# revision identifiers, used by Alembic.
revision = "51dcc8f487fb"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant",
        sa.Column("assistant_id", sa.Text(), server_default=sa.text("gen_random_uuid()::text"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("graph_id", sa.Text(), nullable=False),
        sa.Column(
            "config",
            agent_server.repo.orm.JsonbSafe(astext_type=Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "context",
            agent_server.repo.orm.JsonbSafe(astext_type=Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "metadata",
            agent_server.repo.orm.JsonbSafe(astext_type=Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("assistant_id"),
    )
    op.create_index("idx_assistant_user", "assistant", ["user_id"], unique=False)
    op.create_index("idx_assistant_user_assistant", "assistant", ["user_id", "assistant_id"], unique=True)
    op.create_index(
        "idx_assistant_user_graph_config",
        "assistant",
        ["user_id", "graph_id", sa.literal_column("md5(config::text)")],
        unique=True,
    )
    op.create_index(
        "idx_assistant_metadata_gin",
        "assistant",
        ["metadata"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"metadata": "jsonb_path_ops"},
    )
    op.create_table(
        "thread",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'idle'"), nullable=False),
        sa.Column(
            "metadata_json",
            agent_server.repo.orm.JsonbSafe(astext_type=Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("thread_id"),
    )
    op.create_index("idx_thread_user", "thread", ["user_id"], unique=False)
    op.create_index(
        "idx_thread_metadata_gin",
        "thread",
        ["metadata_json"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"metadata_json": "jsonb_path_ops"},
    )
    op.create_table(
        "assistant_versions",
        sa.Column("assistant_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("graph_id", sa.Text(), nullable=False),
        sa.Column("config", agent_server.repo.orm.JsonbSafe(astext_type=Text()), nullable=True),
        sa.Column("context", agent_server.repo.orm.JsonbSafe(astext_type=Text()), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "metadata",
            agent_server.repo.orm.JsonbSafe(astext_type=Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["assistant_id"], ["assistant.assistant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("assistant_id", "version"),
    )
    op.create_table(
        "crons",
        sa.Column("cron_id", sa.Text(), server_default=sa.text("gen_random_uuid()::text"), nullable=False),
        sa.Column("assistant_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("schedule", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            agent_server.repo.orm.JsonbSafe(astext_type=Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            agent_server.repo.orm.JsonbSafe(astext_type=Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("on_run_completed", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("end_time", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_run_date", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("claimed_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assistant_id"], ["assistant.assistant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["thread.thread_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("cron_id"),
    )
    op.create_index("idx_cron_assistant_id", "crons", ["assistant_id"], unique=False)
    op.create_index("idx_cron_next_run", "crons", ["enabled", "next_run_date"], unique=False)
    op.create_index("idx_cron_thread_id", "crons", ["thread_id"], unique=False)
    op.create_index("idx_cron_user", "crons", ["user_id"], unique=False)
    op.create_table(
        "runs",
        sa.Column("run_id", sa.Text(), server_default=sa.text("gen_random_uuid()::text"), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("assistant_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "input",
            agent_server.repo.orm.JsonbSafe(astext_type=Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.Column("config", agent_server.repo.orm.JsonbSafe(astext_type=Text()), nullable=True),
        sa.Column("context", agent_server.repo.orm.JsonbSafe(astext_type=Text()), nullable=True),
        sa.Column("output", agent_server.repo.orm.JsonbSafe(astext_type=Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("execution_params", agent_server.repo.orm.JsonbSafe(astext_type=Text()), nullable=True),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assistant_id"], ["assistant.assistant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["thread.thread_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("idx_runs_assistant_id", "runs", ["assistant_id"], unique=False)
    op.create_index("idx_runs_created_at", "runs", ["created_at"], unique=False)
    op.create_index("idx_runs_lease_reaper", "runs", ["status", "lease_expires_at"], unique=False)
    op.create_index("idx_runs_status", "runs", ["status"], unique=False)
    op.create_index("idx_runs_thread_id", "runs", ["thread_id"], unique=False)
    op.create_index("idx_runs_user", "runs", ["user_id"], unique=False)
    op.create_table(
        "thread_ttl",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), server_default=sa.text("'delete'"), nullable=False),
        sa.Column("ttl_minutes", sa.Float(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["thread.thread_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("thread_id"),
    )
    op.create_index("idx_thread_ttl_expires_at", "thread_ttl", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_thread_ttl_expires_at", table_name="thread_ttl")
    op.drop_table("thread_ttl")
    op.drop_index("idx_runs_user", table_name="runs")
    op.drop_index("idx_runs_thread_id", table_name="runs")
    op.drop_index("idx_runs_status", table_name="runs")
    op.drop_index("idx_runs_lease_reaper", table_name="runs")
    op.drop_index("idx_runs_created_at", table_name="runs")
    op.drop_index("idx_runs_assistant_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("idx_cron_user", table_name="crons")
    op.drop_index("idx_cron_thread_id", table_name="crons")
    op.drop_index("idx_cron_next_run", table_name="crons")
    op.drop_index("idx_cron_assistant_id", table_name="crons")
    op.drop_table("crons")
    op.drop_table("assistant_versions")
    op.drop_index("idx_thread_metadata_gin", table_name="thread")
    op.drop_index("idx_thread_user", table_name="thread")
    op.drop_table("thread")
    op.drop_index("idx_assistant_user_graph_config", table_name="assistant")
    op.drop_index("idx_assistant_user_assistant", table_name="assistant")
    op.drop_index("idx_assistant_metadata_gin", table_name="assistant")
    op.drop_index("idx_assistant_user", table_name="assistant")
    op.drop_table("assistant")
