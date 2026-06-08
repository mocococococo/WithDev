from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser
from app.models.team import Team, TeamMember
from app.models.user import User
from app.services.user_initialization_service import upsert_authenticated_user


async def get_current_db_user(
    session: AsyncSession,
    auth_user: AuthenticatedUser,
) -> User:
    result = await session.execute(
        select(User).where(User.firebase_uid == auth_user.uid)
    )
    user = result.scalar_one_or_none()
    if user is not None and not user.is_deleted:
        return user

    return await upsert_authenticated_user(session=session, auth_user=auth_user)


async def require_team_member(
    session: AsyncSession,
    auth_user: AuthenticatedUser,
    team_id: UUID,
) -> tuple[User, Team]:
    user = await get_current_db_user(session=session, auth_user=auth_user)

    team_result = await session.execute(
        select(Team).where(Team.id == team_id, Team.is_deleted.is_(False))
    )
    team = team_result.scalar_one_or_none()
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="team not found",
        )

    member_result = await session.execute(
        select(TeamMember).where(
            TeamMember.user_id == user.id,
            TeamMember.team_id == team_id,
            TeamMember.left_at.is_(None),
        )
    )
    if member_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="team access is forbidden",
        )

    return user, team
