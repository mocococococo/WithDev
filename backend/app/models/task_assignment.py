from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class TaskAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_assignments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'selecting', 'ready', 'sending', 'waiting', "
            "'negotiating', 'assigned', 'expired', 'exhausted', 'cancelled', 'failed')",
            name="task_assignment_status",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("tasks.id"), unique=True, nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    candidates: Mapped[list[dict] | None] = mapped_column(JSONB)
    task_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task = relationship("Task", back_populates="assignment")


class TaskAssignmentAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_assignment_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('sending', 'waiting', 'negotiating', 'accepted', 'declined', "
            "'expired', 'cancelled', 'failed')", name="task_assignment_attempt_status",
        ),
    )

    assignment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("task_assignments.id"), nullable=False, index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    connection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("slack_connections.id"), nullable=False,
    )
    slack_user_id: Mapped[str | None] = mapped_column(String(64))
    channel_id: Mapped[str | None] = mapped_column(String(64))
    slack_ts: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    proposed_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history: Mapped[list[dict] | None] = mapped_column(JSONB)


class TaskAssignmentEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable, deduplicated inbox for Slack callbacks; acknowledged before AI/network work."""

    __tablename__ = "task_assignment_events"

    external_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
