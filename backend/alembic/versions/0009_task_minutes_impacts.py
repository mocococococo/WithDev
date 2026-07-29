"""add task minutes impacts

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_minutes_impacts",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("minutes_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
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
            "action IN ('created', 'updated')",
            name="task_minutes_impact_action",
        ),
        sa.ForeignKeyConstraint(["minutes_id"], ["minutes.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_task_minutes_impacts_minutes_id"),
        "task_minutes_impacts",
        ["minutes_id"],
    )
    op.create_index(
        op.f("ix_task_minutes_impacts_task_id"),
        "task_minutes_impacts",
        ["task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_task_minutes_impacts_task_id"),
        table_name="task_minutes_impacts",
    )
    op.drop_index(
        op.f("ix_task_minutes_impacts_minutes_id"),
        table_name="task_minutes_impacts",
    )
    op.drop_table("task_minutes_impacts")
