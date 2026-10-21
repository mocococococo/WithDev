"""Keep separate rounds for manually restarted task assignments.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("task_assignments", "task_assignment_attempts"):
        op.add_column(table, sa.Column("round_number", sa.Integer(), server_default=sa.text("1"), nullable=False))


def downgrade():
    for table in ("task_assignment_attempts", "task_assignments"):
        op.drop_column(table, "round_number")
