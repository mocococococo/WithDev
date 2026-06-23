"""add team invites

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_team_invites_created_by_user_id_users")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_team_invites_team_id_teams")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_invites")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_team_invites_token_hash")),
    )
    op.create_index(op.f("ix_team_invites_team_id"), "team_invites", ["team_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_team_invites_team_id"), table_name="team_invites")
    op.drop_table("team_invites")