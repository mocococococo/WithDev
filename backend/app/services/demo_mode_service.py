from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser
from app.models.meeting import Meeting
from app.models.team import Team, TeamMember
from app.models.user import User
from app.services.aiboard_service import create_aiboard_meeting
from app.services.team_invite_service import TeamSummary
from app.services.user_initialization_service import upsert_authenticated_user


DEMO_TEAM_NAME = "デモチーム"
DEMO_MEETING_TITLE = "新しいサービス"
DEMO_MEETING_THEME = "サービスに関する発案"
DEMO_MEMBERS = (
    ("demo-member-1@withdev.invalid", "デモメンバー1"),
    ("demo-member-2@withdev.invalid", "デモメンバー2"),
)


@dataclass(frozen=True)
class DemoModeResult:
    team: TeamSummary
    meeting_id: UUID


async def create_demo_mode_workspace(
    *,
    session: AsyncSession,
    auth_user: AuthenticatedUser,
    aiboard_api_base_url: str | None,
    aiboard_api_key: str | None,
) -> DemoModeResult:
    current_user = await upsert_authenticated_user(session=session, auth_user=auth_user)
    team = Team(name=DEMO_TEAM_NAME, is_deleted=False)
    session.add(team)
    await session.flush()

    demo_users = [
        User(
            firebase_uid=f"withdev-demo-{team.id}-member-{index}",
            email=email,
            display_name=display_name,
            photo_url=None,
            is_deleted=False,
        )
        for index, (email, display_name) in enumerate(DEMO_MEMBERS, start=1)
    ]
    for demo_user in demo_users:
        session.add(demo_user)
    await session.flush()

    session.add(TeamMember(user_id=current_user.id, team_id=team.id, role="owner"))
    for demo_user in demo_users:
        session.add(TeamMember(user_id=demo_user.id, team_id=team.id, role="member"))

    host_email = (auth_user.email or auth_user.claims.get("email") or "").strip()
    created = await create_aiboard_meeting(
        api_base_url=aiboard_api_base_url,
        api_key=aiboard_api_key,
        title=DEMO_MEETING_TITLE,
        theme=DEMO_MEETING_THEME,
        host_email=host_email,
        team_id=team.id,
    )
    session.add(
        Meeting(
            id=created.id,
            team_id=team.id,
            title=created.title,
            themes=created.themes,
            status="active",
            started_at=_datetime_from_milliseconds(created.created_at_ms),
            ended_at=None,
            aiboard_payload=created.payload,
            is_deleted=False,
        )
    )
    await session.commit()

    return DemoModeResult(
        team=TeamSummary(
            id=team.id,
            name=team.name,
            role="owner",
            member_count=1 + len(demo_users),
        ),
        meeting_id=created.id,
    )


def _datetime_from_milliseconds(value: int | float | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.now(timezone.utc)
