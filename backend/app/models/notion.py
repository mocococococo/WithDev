from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class NotionConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notion_connections"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')", name="notion_connection_status"),
    )

    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teams.id"),
        nullable=False,
        index=True,
    )
    notion_workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    notion_workspace_name: Mapped[str | None] = mapped_column(String(255))
    bot_id: Mapped[str | None] = mapped_column(String(255))
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    default_database_id: Mapped[str | None] = mapped_column(String(255))
    default_database_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    team = relationship("Team", back_populates="notion_connections")
    notion_sync_logs = relationship("NotionSyncLog", back_populates="notion_connection")


class NotionOAuthState(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notion_oauth_states"

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

    team = relationship("Team", back_populates="notion_oauth_states")


class NotionSyncLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notion_sync_logs"
    __table_args__ = (
        CheckConstraint("status IN ('success', 'failed')", name="notion_sync_log_status"),
    )

    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("tasks.id"),
        nullable=False,
        index=True,
    )
    notion_connection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("notion_connections.id"),
        nullable=False,
    )
    notion_page_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    task = relationship("Task", back_populates="notion_sync_logs")
    notion_connection = relationship("NotionConnection", back_populates="notion_sync_logs")


class AiboardTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "aiboard_tasks"
    __table_args__ = (
        CheckConstraint("status IN ('todo', 'in_progress', 'done')", name="aiboard_task_status"),
        Index("ix_aiboard_tasks_meeting", "aiboard_meeting_id"),
    )

    aiboard_meeting_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    source_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notion_page_id: Mapped[str | None] = mapped_column(String(255))
    notion_last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    notion_sync_logs = relationship("AiboardNotionSyncLog", back_populates="aiboard_task")


class AiboardNotionConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "aiboard_notion_connections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="aiboard_notion_connection_status",
        ),
        Index("ix_aiboard_notion_connections_meeting", "aiboard_meeting_id"),
    )

    aiboard_meeting_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    notion_workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    notion_workspace_name: Mapped[str | None] = mapped_column(String(255))
    bot_id: Mapped[str | None] = mapped_column(String(255))
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    default_database_id: Mapped[str | None] = mapped_column(String(255))
    default_database_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    notion_sync_logs = relationship(
        "AiboardNotionSyncLog",
        back_populates="aiboard_notion_connection",
    )


class AiboardNotionOAuthState(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "aiboard_notion_oauth_states"

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


class AiboardNotionSyncLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "aiboard_notion_sync_logs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'failed')",
            name="aiboard_notion_sync_log_status",
        ),
        Index("ix_aiboard_notion_sync_logs_meeting", "aiboard_meeting_id"),
    )

    aiboard_meeting_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    aiboard_task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("aiboard_tasks.id"),
        nullable=False,
        index=True,
    )
    aiboard_notion_connection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("aiboard_notion_connections.id"),
        nullable=False,
    )
    notion_page_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    aiboard_task = relationship("AiboardTask", back_populates="notion_sync_logs")
    aiboard_notion_connection = relationship(
        "AiboardNotionConnection",
        back_populates="notion_sync_logs",
    )
