"""replace meeting theme with themes

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column(
            "themes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE meetings
        SET themes = jsonb_build_array(jsonb_build_object('title', theme))
        WHERE theme IS NOT NULL AND btrim(theme) <> ''
        """
    )
    op.drop_column("meetings", "theme")


def downgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("theme", sa.Text(), nullable=True),
    )
    op.execute(
        """
        UPDATE meetings
        SET theme = themes -> 0 ->> 'title'
        WHERE jsonb_typeof(themes) = 'array' AND jsonb_array_length(themes) > 0
        """
    )
    op.drop_column("meetings", "themes")
