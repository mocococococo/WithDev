"""add aiboard standalone slack tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "aiboard_slack_connections",
        sa.Column("aiboard_meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slack_team_id", sa.String(length=64), nullable=False),
        sa.Column("slack_team_name", sa.String(length=255), nullable=True),
        sa.Column("bot_user_id", sa.String(length=64), nullable=True),
        sa.Column("bot_access_token", sa.Text(), nullable=False),
        sa.Column("default_channel_id", sa.String(length=64), nullable=True),
        sa.Column("default_channel_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
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
            name="aiboard_slack_connection_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_aiboard_slack_connections_meeting",
        "aiboard_slack_connections",
        ["aiboard_meeting_id"],
        unique=False,
    )

    op.create_table(
        "aiboard_slack_oauth_states",
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
        op.f("ix_aiboard_slack_oauth_states_aiboard_meeting_id"),
        "aiboard_slack_oauth_states",
        ["aiboard_meeting_id"],
        unique=False,
    )

    op.create_table(
        "aiboard_slack_post_logs",
        sa.Column("aiboard_meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "aiboard_slack_connection_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("channel_name", sa.String(length=255), nullable=True),
        sa.Column("slack_ts", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
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
            name="aiboard_slack_post_log_status",
        ),
        sa.ForeignKeyConstraint(
            ["aiboard_slack_connection_id"],
            ["aiboard_slack_connections.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_aiboard_slack_post_logs_meeting",
        "aiboard_slack_post_logs",
        ["aiboard_meeting_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_aiboard_slack_post_logs_meeting", table_name="aiboard_slack_post_logs")
    op.drop_table("aiboard_slack_post_logs")
    op.drop_index(
        op.f("ix_aiboard_slack_oauth_states_aiboard_meeting_id"),
        table_name="aiboard_slack_oauth_states",
    )
    op.drop_table("aiboard_slack_oauth_states")
    op.drop_index(
        "ix_aiboard_slack_connections_meeting",
        table_name="aiboard_slack_connections",
    )
    op.drop_table("aiboard_slack_connections")
