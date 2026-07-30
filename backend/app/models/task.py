from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status IN ('todo', 'in_progress', 'done')", name="task_status"),
    )

    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teams.id"),
        nullable=False,
        index=True,
    )
    source_minutes_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("minutes.id"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id"),
        index=True,
    )
    assignee_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notion_page_id: Mapped[str | None] = mapped_column(String(255))
    notion_last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    team = relationship("Team", back_populates="tasks")
    source_minutes = relationship("MeetingMinutes", back_populates="source_tasks")
    assignee_user = relationship("User", back_populates="assigned_tasks")
    notion_sync_logs = relationship("NotionSyncLog", back_populates="task")
    minutes_impacts = relationship("TaskMinutesImpact", back_populates="task")
    roadmap = relationship(
        "TaskRoadmap",
        back_populates="task",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TaskRoadmap(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_roadmaps"
    __table_args__ = (
        CheckConstraint(
            "generation_status IN ('pending', 'generating', 'ready', 'failed')",
            name="task_roadmap_generation_status",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("tasks.id"),
        unique=True,
        nullable=False,
    )
    overview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generation_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
    )
    generation_error: Mapped[str | None] = mapped_column(Text)
    generation_token: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
    )
    generation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    input_hash: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    has_source_updates: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    last_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task = relationship("Task", back_populates="roadmap")
    steps = relationship(
        "TaskRoadmapStep",
        back_populates="roadmap",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TaskRoadmapStep.position",
    )


class TaskRoadmapStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_roadmap_steps"
    __table_args__ = (
        CheckConstraint(
            "status IN ('todo', 'in_progress', 'done')",
            name="task_roadmap_step_status",
        ),
        CheckConstraint(
            "source IN ('ai', 'user')",
            name="task_roadmap_step_source",
        ),
        UniqueConstraint(
            "roadmap_id",
            "position",
            name="task_roadmap_step_position",
        ),
    )

    roadmap_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("task_roadmaps.id"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="todo")
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    user_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    roadmap = relationship("TaskRoadmap", back_populates="steps")


class TaskMinutesImpact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_minutes_impacts"
    __table_args__ = (
        CheckConstraint("action IN ('created', 'updated')", name="task_minutes_impact_action"),
    )

    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("tasks.id"),
        nullable=False,
        index=True,
    )
    minutes_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("minutes.id"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)

    task = relationship("Task", back_populates="minutes_impacts")
    minutes = relationship("MeetingMinutes", back_populates="task_impacts")


class TaskGenerationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_generation_runs"
    __table_args__ = (
        UniqueConstraint(
            "minutes_id",
            "input_hash",
            "prompt_version",
            name="task_generation_run_input",
        ),
    )

    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teams.id"),
        nullable=False,
        index=True,
    )
    minutes_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("minutes.id"),
        nullable=False,
        index=True,
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
