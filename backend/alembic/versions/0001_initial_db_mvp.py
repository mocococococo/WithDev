"""initial db mvp

Revision ID: 0001
Revises:
Create Date: 2026-06-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("firebase_uid", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("photo_url", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("firebase_uid", name=op.f("uq_users_firebase_uid")),
    )

    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
    )

    op.create_table(
        "team_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name=op.f("ck_team_members_team_member_role")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_team_members_team_id_teams")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_team_members_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_members")),
    )
    op.create_index(
        "ix_team_members_active_user_team",
        "team_members",
        ["user_id", "team_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
    )

    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("theme", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'ended')", name=op.f("ck_meetings_meeting_status")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_meetings_team_id_teams")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meetings")),
    )
    op.create_index(op.f("ix_meetings_team_id"), "meetings", ["team_id"], unique=False)

    op.create_table(
        "slack_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slack_team_id", sa.String(length=64), nullable=False),
        sa.Column("slack_team_name", sa.String(length=255), nullable=True),
        sa.Column("bot_user_id", sa.String(length=64), nullable=True),
        sa.Column("bot_access_token", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'revoked')", name=op.f("ck_slack_connections_slack_connection_status")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_slack_connections_team_id_teams")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slack_connections")),
    )
    op.create_index(op.f("ix_slack_connections_team_id"), "slack_connections", ["team_id"], unique=False)

    op.create_table(
        "slack_oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_slack_oauth_states_team_id_teams")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slack_oauth_states")),
        sa.UniqueConstraint("state", name=op.f("uq_slack_oauth_states_state")),
    )
    op.create_index(op.f("ix_slack_oauth_states_team_id"), "slack_oauth_states", ["team_id"], unique=False)

    op.create_table(
        "minutes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], name=op.f("fk_minutes_meeting_id_meetings")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_minutes")),
        sa.UniqueConstraint("meeting_id", name=op.f("uq_minutes_meeting_id")),
    )

    op.create_table(
        "slack_post_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("minutes_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slack_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("channel_name", sa.String(length=255), nullable=True),
        sa.Column("slack_ts", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('success', 'failed')", name=op.f("ck_slack_post_logs_slack_post_log_status")),
        sa.ForeignKeyConstraint(["minutes_id"], ["minutes.id"], name=op.f("fk_slack_post_logs_minutes_id_minutes")),
        sa.ForeignKeyConstraint(
            ["slack_connection_id"],
            ["slack_connections.id"],
            name=op.f("fk_slack_post_logs_slack_connection_id_slack_connections"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slack_post_logs")),
    )
    op.create_index(op.f("ix_slack_post_logs_minutes_id"), "slack_post_logs", ["minutes_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_slack_post_logs_minutes_id"), table_name="slack_post_logs")
    op.drop_table("slack_post_logs")
    op.drop_table("minutes")
    op.drop_index(op.f("ix_slack_oauth_states_team_id"), table_name="slack_oauth_states")
    op.drop_table("slack_oauth_states")
    op.drop_index(op.f("ix_slack_connections_team_id"), table_name="slack_connections")
    op.drop_table("slack_connections")
    op.drop_index(op.f("ix_meetings_team_id"), table_name="meetings")
    op.drop_table("meetings")
    op.drop_index("ix_team_members_active_user_team", table_name="team_members")
    op.drop_table("team_members")
    op.drop_table("teams")
    op.drop_table("users")
