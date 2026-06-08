"""add tasks mvp

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_minutes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignee_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('todo', 'doing', 'done')", name=op.f("ck_tasks_task_status")),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], name=op.f("fk_tasks_assignee_user_id_users")),
        sa.ForeignKeyConstraint(["source_minutes_id"], ["minutes.id"], name=op.f("fk_tasks_source_minutes_id_minutes")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_tasks_team_id_teams")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index(op.f("ix_tasks_assignee_user_id"), "tasks", ["assignee_user_id"], unique=False)
    op.create_index(op.f("ix_tasks_source_minutes_id"), "tasks", ["source_minutes_id"], unique=False)
    op.create_index(op.f("ix_tasks_team_id"), "tasks", ["team_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tasks_team_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_source_minutes_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_assignee_user_id"), table_name="tasks")
    op.drop_table("tasks")
