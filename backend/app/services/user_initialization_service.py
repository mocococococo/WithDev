from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser
from app.models.team import Team, TeamMember
from app.models.user import User


@dataclass(frozen=True)
class InitializedTeam:
    id: UUID
    name: str
    role: str


@dataclass(frozen=True)
class InitializedUser:
    id: UUID
    firebase_uid: str
    email: str
    display_name: str
    photo_url: str | None


@dataclass(frozen=True)
class InitializedUserContext:
    user: InitializedUser
    teams: list[InitializedTeam]


def _profile_from_auth(auth_user: AuthenticatedUser) -> tuple[str, str, str | None]:
    email = auth_user.email or auth_user.claims.get("email") or ""
    display_name = auth_user.claims.get("name") or email or "User"
    photo_url = auth_user.claims.get("picture")
    return email, display_name, photo_url


async def initialize_user_context(
    session: AsyncSession,
    auth_user: AuthenticatedUser,
) -> InitializedUserContext:
    user = await upsert_authenticated_user(session=session, auth_user=auth_user)

    teams = await _get_active_teams(session=session, user_id=user.id)
    if not teams:
        _, display_name, _ = _profile_from_auth(auth_user)
        await _create_default_team(
            session=session,
            user_id=user.id,
            display_name=display_name,
        )
        teams = await _get_active_teams(session=session, user_id=user.id)

    await session.commit()

    return InitializedUserContext(
        user=InitializedUser(
            id=user.id,
            firebase_uid=user.firebase_uid,
            email=user.email,
            display_name=user.display_name,
            photo_url=user.photo_url,
        ),
        teams=teams,
    )


async def upsert_authenticated_user(
    session: AsyncSession,
    auth_user: AuthenticatedUser,
) -> User:
    email, display_name, photo_url = _profile_from_auth(auth_user)
    return await _upsert_user(
        session=session,
        firebase_uid=auth_user.uid,
        email=email,
        display_name=display_name,
        photo_url=photo_url,
    )


async def _upsert_user(
    session: AsyncSession,
    firebase_uid: str,
    email: str,
    display_name: str,
    photo_url: str | None,
) -> User:
    result = await session.execute(
        select(User).where(User.firebase_uid == firebase_uid)
    )
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            firebase_uid=firebase_uid,
            email=email,
            display_name=display_name,
            photo_url=photo_url,
            is_deleted=False,
        )
        session.add(user)
    else:
        user.email = email
        user.display_name = display_name
        user.photo_url = photo_url
        user.is_deleted = False

    await session.flush()
    return user


async def _get_active_teams(
    session: AsyncSession,
    user_id: UUID,
) -> list[InitializedTeam]:
    result = await session.execute(
        select(TeamMember, Team)
        .join(Team, Team.id == TeamMember.team_id)
        .where(
            TeamMember.user_id == user_id,
            TeamMember.left_at.is_(None),
            Team.is_deleted.is_(False),
        )
        .order_by(TeamMember.created_at, Team.name)
    )

    return [
        InitializedTeam(id=team.id, name=team.name, role=membership.role)
        for membership, team in result.all()
    ]


async def _create_default_team(
    session: AsyncSession,
    user_id: UUID,
    display_name: str,
) -> None:
    team = Team(name=f"{display_name} のチーム", is_deleted=False)
    session.add(team)
    await session.flush()

    session.add(
        TeamMember(
            user_id=user_id,
            team_id=team.id,
            role="owner",
        )
    )
    await session.flush()
