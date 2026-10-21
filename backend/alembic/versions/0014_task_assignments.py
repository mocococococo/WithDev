"""Slack task assignment agent.

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade():
    op.create_table(
        "task_assignments",
        *_timestamps(),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("candidates", JSONB),
        sa.Column("task_snapshot", JSONB),
        sa.Column("error", sa.Text),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'selecting', 'ready', 'sending', 'waiting', 'negotiating', "
            "'assigned', 'expired', 'exhausted', 'cancelled', 'failed')", name="task_assignment_status",
        ),
    )
    op.create_index("ix_task_assignments_status", "task_assignments", ["status"])
    op.create_table(
        "task_assignment_attempts",
        *_timestamps(),
        sa.Column("assignment_id", UUID(as_uuid=True), sa.ForeignKey("task_assignments.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("connection_id", UUID(as_uuid=True), sa.ForeignKey("slack_connections.id"), nullable=False),
        sa.Column("slack_user_id", sa.String(64)),
        sa.Column("channel_id", sa.String(64)),
        sa.Column("slack_ts", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("proposed_due_at", sa.DateTime(timezone=True)),
        sa.Column("history", JSONB),
        sa.CheckConstraint(
            "status IN ('sending', 'waiting', 'negotiating', 'accepted', 'declined', "
            "'expired', 'cancelled', 'failed')", name="task_assignment_attempt_status",
        ),
    )
    op.create_index("ix_task_assignment_attempts_assignment_id", "task_assignment_attempts", ["assignment_id"])
    op.create_index("ix_task_assignment_attempts_expires_at", "task_assignment_attempts", ["expires_at"])
    op.create_table(
        "task_assignment_events",
        *_timestamps(),
        sa.Column("external_id", sa.String(128), nullable=False, unique=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
    )
    op.create_index("ix_task_assignment_events_status", "task_assignment_events", ["status"])


def downgrade():
    op.drop_table("task_assignment_events")
    op.drop_table("task_assignment_attempts")
    op.drop_table("task_assignments")
