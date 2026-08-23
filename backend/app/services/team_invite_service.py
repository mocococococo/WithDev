from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser
from app.models.team import Team, TeamInvite, TeamMember
from app.models.user import User
from app.services.team_access_service import get_current_db_user, require_team_member

INVITE_LIFETIME = timedelta(days=7)


@dataclass(frozen=True)
class TeamSummary:
    id: UUID
    name: str
    role: str
    member_count: int


@dataclass(frozen=True)
class InviteSummary:
    id: UUID
    team_id: UUID
    created_by_user_id: UUID
    created_by_name: str
    created_at: datetime
    expires_at: datetime
    status: str
    can_revoke: bool


@dataclass(frozen=True)
class InvitePreview:
    team_id: UUID
    team_name: str
    expires_at: datetime
    already_member: bool


def hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def invite_status(invite: TeamInvite, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if invite.revoked_at is not None:
        return "revoked"
    if invite.expires_at <= current:
        return "expired"
    return "active"


async def create_team(session: AsyncSession, auth_user: AuthenticatedUser, name: str) -> TeamSummary:
    normalized_name = name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="team name is required")
    if len(normalized_name) > 255:
        raise HTTPException(status_code=400, detail="team name is too long")
    user = await get_current_db_user(session=session, auth_user=auth_user)
    team = Team(name=normalized_name, is_deleted=False)
    session.add(team)
    await session.flush()
    session.add(TeamMember(user_id=user.id, team_id=team.id, role="owner"))
    await session.commit()
    return TeamSummary(id=team.id, name=team.name, role="owner", member_count=1)


async def delete_team(
    session: AsyncSession,
    auth_user: AuthenticatedUser,
    team_id: UUID,
    confirmation_name: str,
) -> None:
    user, team = await require_team_member(
        session=session,
        auth_user=auth_user,
        team_id=team_id,
    )
    membership = await _active_membership(session, user.id, team_id)
    if membership.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="team delete is forbidden",
        )
    if confirmation_name != team.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="team name does not match",
        )

    team.is_deleted = True
    await session.commit()


async def list_team_invites(session: AsyncSession, auth_user: AuthenticatedUser, team_id: UUID) -> list[InviteSummary]:
    user, _ = await require_team_member(session=session, auth_user=auth_user, team_id=team_id)
    membership = await _active_membership(session, user.id, team_id)
    result = await session.execute(
        select(TeamInvite, User)
        .join(User, User.id == TeamInvite.created_by_user_id)
        .where(TeamInvite.team_id == team_id)
        .order_by(TeamInvite.created_at.desc())
    )
    return [_invite_summary(invite, creator, user.id, membership.role) for invite, creator in result.all()]


async def create_team_invite(session: AsyncSession, auth_user: AuthenticatedUser, team_id: UUID) -> tuple[InviteSummary, str]:
    user, _ = await require_team_member(session=session, auth_user=auth_user, team_id=team_id)
    membership = await _active_membership(session, user.id, team_id)
    token = secrets.token_urlsafe(32)
    invite = TeamInvite(
        team_id=team_id,
        created_by_user_id=user.id,
        token_hash=hash_invite_token(token),
        expires_at=datetime.now(timezone.utc) + INVITE_LIFETIME,
    )
    session.add(invite)
    await session.commit()
    await session.refresh(invite)
    return _invite_summary(invite, user, user.id, membership.role), token


async def revoke_team_invite(session: AsyncSession, auth_user: AuthenticatedUser, team_id: UUID, invite_id: UUID) -> None:
    user, _ = await require_team_member(session=session, auth_user=auth_user, team_id=team_id)
    membership = await _active_membership(session, user.id, team_id)
    result = await session.execute(select(TeamInvite).where(TeamInvite.id == invite_id, TeamInvite.team_id == team_id))
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="invite not found")
    if invite.created_by_user_id != user.id and membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="invite revoke is forbidden")
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(timezone.utc)
        await session.commit()


async def get_invite_preview(session: AsyncSession, auth_user: AuthenticatedUser, token: str) -> InvitePreview:
    user = await get_current_db_user(session=session, auth_user=auth_user)
    invite, team = await _valid_invite(session, token)
    result = await session.execute(
        select(TeamMember.id).where(
            TeamMember.user_id == user.id,
            TeamMember.team_id == team.id,
            TeamMember.left_at.is_(None),
        )
    )
    return InvitePreview(team_id=team.id, team_name=team.name, expires_at=invite.expires_at, already_member=result.scalar_one_or_none() is not None)


async def accept_team_invite(session: AsyncSession, auth_user: AuthenticatedUser, token: str) -> TeamSummary:
    user = await get_current_db_user(session=session, auth_user=auth_user)
    _, team = await _valid_invite(session, token)
    result = await session.execute(
        select(TeamMember)
        .where(TeamMember.user_id == user.id, TeamMember.team_id == team.id)
        .order_by(TeamMember.created_at.desc())
    )
    memberships = list(result.scalars())
    active = next((item for item in memberships if item.left_at is None), None)
    if active is None:
        previous = memberships[0] if memberships else None
        if previous is None:
            active = TeamMember(user_id=user.id, team_id=team.id, role="member")
            session.add(active)
        else:
            previous.left_at = None
            previous.role = "member"
            active = previous
        await session.commit()
    return TeamSummary(id=team.id, name=team.name, role=active.role, member_count=await _member_count(session, team.id))


async def _valid_invite(session: AsyncSession, token: str) -> tuple[TeamInvite, Team]:
    result = await session.execute(
        select(TeamInvite, Team)
        .join(Team, Team.id == TeamInvite.team_id)
        .where(TeamInvite.token_hash == hash_invite_token(token), Team.is_deleted.is_(False))
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="invite not found")
    invite, team = row
    current_status = invite_status(invite)
    if current_status == "revoked":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invite is revoked")
    if current_status == "expired":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invite is expired")
    return invite, team


async def _active_membership(session: AsyncSession, user_id: UUID, team_id: UUID) -> TeamMember:
    result = await session.execute(
        select(TeamMember).where(TeamMember.user_id == user_id, TeamMember.team_id == team_id, TeamMember.left_at.is_(None))
    )
    return result.scalar_one()


async def _member_count(session: AsyncSession, team_id: UUID) -> int:
    result = await session.execute(select(func.count(TeamMember.id)).where(TeamMember.team_id == team_id, TeamMember.left_at.is_(None)))
    return int(result.scalar_one())


def _invite_summary(invite: TeamInvite, creator: User, current_user_id: UUID, current_role: str) -> InviteSummary:
    return InviteSummary(
        id=invite.id,
        team_id=invite.team_id,
        created_by_user_id=invite.created_by_user_id,
        created_by_name=creator.display_name,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        status=invite_status(invite),
        can_revoke=invite.created_by_user_id == current_user_id or current_role in {"owner", "admin"},
    )
