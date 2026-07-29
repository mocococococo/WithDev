"""add task generation runs

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_generation_runs",
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("minutes_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["minutes_id"], ["minutes.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "minutes_id",
            "input_hash",
            "prompt_version",
            name="task_generation_run_input",
        ),
    )
    op.create_index(
        op.f("ix_task_generation_runs_minutes_id"),
        "task_generation_runs",
        ["minutes_id"],
    )
    op.create_index(
        op.f("ix_task_generation_runs_team_id"),
        "task_generation_runs",
        ["team_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_task_generation_runs_team_id"),
        table_name="task_generation_runs",
    )
    op.drop_index(
        op.f("ix_task_generation_runs_minutes_id"),
        table_name="task_generation_runs",
    )
    op.drop_table("task_generation_runs")
