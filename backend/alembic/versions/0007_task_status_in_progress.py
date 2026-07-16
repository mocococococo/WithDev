"""use in_progress task status

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("task_status", "tasks", type_="check")
    op.execute("UPDATE tasks SET status = 'in_progress' WHERE status = 'doing'")
    op.create_check_constraint(
        "task_status",
        "tasks",
        "status IN ('todo', 'in_progress', 'done')",
    )


def downgrade() -> None:
    op.drop_constraint("task_status", "tasks", type_="check")
    op.execute("UPDATE tasks SET status = 'doing' WHERE status = 'in_progress'")
    op.create_check_constraint(
        "task_status",
        "tasks",
        "status IN ('todo', 'doing', 'done')",
    )