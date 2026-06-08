from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Team(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    members = relationship("TeamMember", back_populates="team")
    meetings = relationship("Meeting", back_populates="team")
    tasks = relationship("Task", back_populates="team")
    slack_connections = relationship("SlackConnection", back_populates="team")
    slack_oauth_states = relationship("SlackOAuthState", back_populates="team")


class TeamMember(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "team_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="team_member_role"),
        Index(
            "ix_team_members_active_user_team",
            "user_id",
            "team_id",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teams.id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user = relationship("User", back_populates="team_memberships")
    team = relationship("Team", back_populates="members")
