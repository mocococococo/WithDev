"""add task roadmaps

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_roadmaps",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("overview", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "generation_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("generation_error", sa.Text(), nullable=True),
        sa.Column("generation_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "has_source_updates",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_generated_at", sa.DateTime(timezone=True), nullable=True),
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
            "generation_status IN ('pending', 'generating', 'ready', 'failed')",
            name=op.f("ck_task_roadmaps_task_roadmap_generation_status"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_roadmaps_task_id_tasks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_roadmaps")),
        sa.UniqueConstraint("task_id", name=op.f("uq_task_roadmaps_task_id")),
    )
    op.create_table(
        "task_roadmap_steps",
        sa.Column("roadmap_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="todo",
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "user_edited",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
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
            "source IN ('ai', 'user')",
            name=op.f("ck_task_roadmap_steps_task_roadmap_step_source"),
        ),
        sa.CheckConstraint(
            "status IN ('todo', 'in_progress', 'done')",
            name=op.f("ck_task_roadmap_steps_task_roadmap_step_status"),
        ),
        sa.ForeignKeyConstraint(
            ["roadmap_id"],
            ["task_roadmaps.id"],
            name=op.f("fk_task_roadmap_steps_roadmap_id_task_roadmaps"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_roadmap_steps")),
        sa.UniqueConstraint(
            "roadmap_id",
            "position",
            name="task_roadmap_step_position",
        ),
    )
    op.create_index(
        op.f("ix_task_roadmap_steps_roadmap_id"),
        "task_roadmap_steps",
        ["roadmap_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_task_roadmap_steps_roadmap_id"),
        table_name="task_roadmap_steps",
    )
    op.drop_table("task_roadmap_steps")
    op.drop_table("task_roadmaps")
