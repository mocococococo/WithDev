"""add notion and aiboard tasks

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("notion_page_id", sa.String(length=255), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("notion_last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "notion_connections",
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notion_workspace_id", sa.String(length=255), nullable=False),
        sa.Column("notion_workspace_name", sa.String(length=255), nullable=True),
        sa.Column("bot_id", sa.String(length=255), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("default_database_id", sa.String(length=255), nullable=True),
        sa.Column("default_database_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="notion_connection_status",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notion_connections_team_id"), "notion_connections", ["team_id"])

    op.create_table(
        "notion_oauth_states",
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state"),
    )
    op.create_index(op.f("ix_notion_oauth_states_team_id"), "notion_oauth_states", ["team_id"])

    op.create_table(
        "notion_sync_logs",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notion_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notion_page_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failed')",
            name="notion_sync_log_status",
        ),
        sa.ForeignKeyConstraint(["notion_connection_id"], ["notion_connections.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notion_sync_logs_task_id"), "notion_sync_logs", ["task_id"])

    op.create_table(
        "aiboard_tasks",
        sa.Column("aiboard_meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("assignee_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notion_page_id", sa.String(length=255), nullable=True),
        sa.Column("notion_last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('todo', 'in_progress', 'done')",
            name="aiboard_task_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_aiboard_tasks_meeting", "aiboard_tasks", ["aiboard_meeting_id"])

    op.create_table(
        "aiboard_notion_connections",
        sa.Column("aiboard_meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notion_workspace_id", sa.String(length=255), nullable=False),
        sa.Column("notion_workspace_name", sa.String(length=255), nullable=True),
        sa.Column("bot_id", sa.String(length=255), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("default_database_id", sa.String(length=255), nullable=True),
        sa.Column("default_database_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="aiboard_notion_connection_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_aiboard_notion_connections_meeting",
        "aiboard_notion_connections",
        ["aiboard_meeting_id"],
    )

    op.create_table(
        "aiboard_notion_oauth_states",
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("aiboard_meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state"),
    )
    op.create_index(
        op.f("ix_aiboard_notion_oauth_states_aiboard_meeting_id"),
        "aiboard_notion_oauth_states",
        ["aiboard_meeting_id"],
    )

    op.create_table(
        "aiboard_notion_sync_logs",
        sa.Column("aiboard_meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aiboard_task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aiboard_notion_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notion_page_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failed')",
            name="aiboard_notion_sync_log_status",
        ),
        sa.ForeignKeyConstraint(["aiboard_notion_connection_id"], ["aiboard_notion_connections.id"]),
        sa.ForeignKeyConstraint(["aiboard_task_id"], ["aiboard_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_aiboard_notion_sync_logs_meeting",
        "aiboard_notion_sync_logs",
        ["aiboard_meeting_id"],
    )
    op.create_index(
        op.f("ix_aiboard_notion_sync_logs_aiboard_task_id"),
        "aiboard_notion_sync_logs",
        ["aiboard_task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_aiboard_notion_sync_logs_aiboard_task_id"),
        table_name="aiboard_notion_sync_logs",
    )
    op.drop_index(
        "ix_aiboard_notion_sync_logs_meeting",
        table_name="aiboard_notion_sync_logs",
    )
    op.drop_table("aiboard_notion_sync_logs")
    op.drop_index(
        op.f("ix_aiboard_notion_oauth_states_aiboard_meeting_id"),
        table_name="aiboard_notion_oauth_states",
    )
    op.drop_table("aiboard_notion_oauth_states")
    op.drop_index(
        "ix_aiboard_notion_connections_meeting",
        table_name="aiboard_notion_connections",
    )
    op.drop_table("aiboard_notion_connections")
    op.drop_index("ix_aiboard_tasks_meeting", table_name="aiboard_tasks")
    op.drop_table("aiboard_tasks")
    op.drop_index(op.f("ix_notion_sync_logs_task_id"), table_name="notion_sync_logs")
    op.drop_table("notion_sync_logs")
    op.drop_index(op.f("ix_notion_oauth_states_team_id"), table_name="notion_oauth_states")
    op.drop_table("notion_oauth_states")
    op.drop_index(op.f("ix_notion_connections_team_id"), table_name="notion_connections")
    op.drop_table("notion_connections")
    op.drop_column("tasks", "notion_last_synced_at")
    op.drop_column("tasks", "notion_page_id")
