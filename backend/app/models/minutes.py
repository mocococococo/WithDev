from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class MeetingMinutes(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "minutes"

    meeting_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("meetings.id"),
        unique=True,
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    meeting = relationship("Meeting", back_populates="minutes")
    slack_post_logs = relationship("SlackPostLog", back_populates="minutes")
    source_tasks = relationship("Task", back_populates="source_minutes")
