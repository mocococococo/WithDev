"""add aiboard payload and slack defaults

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("aiboard_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "slack_connections",
        sa.Column("default_channel_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "slack_connections",
        sa.Column("default_channel_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("slack_connections", "default_channel_name")
    op.drop_column("slack_connections", "default_channel_id")
    op.drop_column("meetings", "aiboard_payload")
