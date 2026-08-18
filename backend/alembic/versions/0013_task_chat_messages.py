"""persist task chat messages

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_chat_messages",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "role IN ('user', 'assistant')",
            name=op.f("ck_task_chat_messages_task_chat_message_role"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_chat_messages_task_id_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_task_chat_messages_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_chat_messages")),
    )
    op.create_index(
        op.f("ix_task_chat_messages_task_id"),
        "task_chat_messages",
        ["task_id"],
    )
    op.create_index(
        op.f("ix_task_chat_messages_user_id"),
        "task_chat_messages",
        ["user_id"],
    )
    op.create_index(
        "ix_task_chat_messages_task_user_created",
        "task_chat_messages",
        ["task_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_chat_messages_task_user_created",
        table_name="task_chat_messages",
    )
    op.drop_index(
        op.f("ix_task_chat_messages_user_id"),
        table_name="task_chat_messages",
    )
    op.drop_index(
        op.f("ix_task_chat_messages_task_id"),
        table_name="task_chat_messages",
    )
    op.drop_table("task_chat_messages")
