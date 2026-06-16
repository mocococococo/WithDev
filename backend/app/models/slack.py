from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SlackConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "slack_connections"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')", name="slack_connection_status"),
    )

    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teams.id"),
        nullable=False,
        index=True,
    )
    slack_team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    slack_team_name: Mapped[str | None] = mapped_column(String(255))
    bot_user_id: Mapped[str | None] = mapped_column(String(64))
    bot_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    default_channel_id: Mapped[str | None] = mapped_column(String(64))
    default_channel_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    team = relationship("Team", back_populates="slack_connections")
    slack_post_logs = relationship("SlackPostLog", back_populates="slack_connection")


class SlackPostLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "slack_post_logs"
    __table_args__ = (
        CheckConstraint("status IN ('success', 'failed')", name="slack_post_log_status"),
    )

    minutes_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("minutes.id"),
        nullable=False,
        index=True,
    )
    slack_connection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("slack_connections.id"),
        nullable=False,
    )
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_name: Mapped[str | None] = mapped_column(String(255))
    slack_ts: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    minutes = relationship("MeetingMinutes", back_populates="slack_post_logs")
    slack_connection = relationship("SlackConnection", back_populates="slack_post_logs")


class SlackOAuthState(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "slack_oauth_states"

    state: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teams.id"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    team = relationship("Team", back_populates="slack_oauth_states")


class AiboardSlackConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "aiboard_slack_connections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="aiboard_slack_connection_status",
        ),
        Index("ix_aiboard_slack_connections_meeting", "aiboard_meeting_id"),
    )

    aiboard_meeting_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    slack_team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    slack_team_name: Mapped[str | None] = mapped_column(String(255))
    bot_user_id: Mapped[str | None] = mapped_column(String(64))
    bot_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    default_channel_id: Mapped[str | None] = mapped_column(String(64))
    default_channel_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    slack_post_logs = relationship(
        "AiboardSlackPostLog",
        back_populates="slack_connection",
    )


class AiboardSlackOAuthState(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "aiboard_slack_oauth_states"

    state: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    aiboard_meeting_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )


class AiboardSlackPostLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "aiboard_slack_post_logs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'failed')",
            name="aiboard_slack_post_log_status",
        ),
        Index("ix_aiboard_slack_post_logs_meeting", "aiboard_meeting_id"),
    )

    aiboard_meeting_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    aiboard_slack_connection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("aiboard_slack_connections.id"),
        nullable=False,
    )
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_name: Mapped[str | None] = mapped_column(String(255))
    slack_ts: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    slack_connection = relationship(
        "AiboardSlackConnection",
        back_populates="slack_post_logs",
    )
