from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.auth import AuthenticatedUser, verify_firebase_user
from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.meeting import Meeting
from app.models.minutes import MeetingMinutes
from app.models.slack import SlackPostLog
from app.services.aiboard_service import (
    AiboardAuthenticationError,
    AiboardConfigurationError,
    AiboardRequestError,
    build_aiboard_launch_url,
    create_aiboard_meeting,
)
from app.services.minutes_service import (
    MAX_TEXT_LENGTH,
    MinutesGenerationError,
    generate_minutes_from_text,
)
from app.services.slack_post_service import (
    SlackConnectionNotFoundError,
    SlackPostError,
    create_slack_post_for_minutes,
)
from app.services.team_access_service import require_team_member


router = APIRouter()
settings = get_settings()
DEFAULT_MINUTES_TITLE = "\u8b70\u4e8b\u9332"


class MeetingBody(BaseModel):
    id: UUID
    team_id: UUID
    title: str
    themes: list[dict[str, Any]] | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime
    participant_count: int = 1


class MeetingListResponse(BaseModel):
    meetings: list[MeetingBody]


class MeetingResponse(BaseModel):
    meeting: MeetingBody


class MeetingCreateResponse(BaseModel):
    meeting: MeetingBody
    launch_url: str


class MeetingCreateRequest(BaseModel):
    title: str | None = None
    theme: str | None = None


class MinutesFromTextRequest(BaseModel):
    text: str | None = None


class MinutesToSlackRequest(BaseModel):
    text: str | None = None
    channel_id: str | None = None


class MinutesBody(BaseModel):
    id: UUID
    meeting_id: UUID
    title: str | None
    body: str
    created_at: datetime
    updated_at: datetime


class MinutesResponse(BaseModel):
    minutes: MinutesBody


class MinutesToSlackPostBody(BaseModel):
    id: UUID
    minutes_id: UUID
    channel_id: str
    channel_name: str | None
    slack_ts: str | None
    status: str
    created_at: datetime


class MinutesToSlackResponse(BaseModel):
    minutes: MinutesBody
    slack_post: MinutesToSlackPostBody


@router.get("/teams/{team_id}/meetings", response_model=MeetingListResponse)
async def list_team_meetings(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> MeetingListResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)

    result = await session.execute(
        select(Meeting)
        .where(Meeting.team_id == team_id, Meeting.is_deleted.is_(False))
        .order_by(Meeting.updated_at.desc(), Meeting.created_at.desc())
    )
    return MeetingListResponse(
        meetings=[_meeting_body(meeting) for meeting in result.scalars().all()]
    )


@router.post(
    "/teams/{team_id}/meetings",
    response_model=MeetingCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_team_meeting(
    team_id: UUID,
    request: MeetingCreateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> MeetingCreateResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)

    title = (request.title or "").strip()
    theme = (request.theme or "").strip() or None
    if not title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="title is required",
        )
    if len(title) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="title must be 255 characters or less",
        )

    host_email = (auth_user.email or auth_user.claims.get("email") or "").strip()
    if not host_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="host email is required",
        )

    try:
        created = await create_aiboard_meeting(
            api_base_url=settings.aiboard_api_base_url,
            api_key=settings.aiboard_api_key,
            title=title,
            theme=theme,
            host_email=host_email,
            team_id=team_id,
        )
        launch_url = build_aiboard_launch_url(
            frontend_base_url=settings.aiboard_frontend_base_url,
            team_id=team_id,
            meeting_id=created.id,
        )
    except AiboardConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AiboardAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except AiboardRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    meeting = Meeting(
        id=created.id,
        team_id=team_id,
        title=created.title,
        themes=created.themes,
        status="active",
        started_at=_datetime_from_milliseconds(created.created_at_ms),
        ended_at=None,
        aiboard_payload=created.payload,
        is_deleted=False,
    )
    session.add(meeting)
    await session.commit()
    await session.refresh(meeting)

    return MeetingCreateResponse(
        meeting=_meeting_body(meeting),
        launch_url=launch_url,
    )


@router.get("/meetings/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
    meeting_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> MeetingResponse:
    meeting = await _get_accessible_meeting(
        session=session,
        auth_user=auth_user,
        meeting_id=meeting_id,
    )
    return MeetingResponse(meeting=_meeting_body(meeting))


@router.get("/meetings/{meeting_id}/minutes", response_model=MinutesResponse)
async def get_meeting_minutes(
    meeting_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> MinutesResponse:
    await _get_accessible_meeting(
        session=session,
        auth_user=auth_user,
        meeting_id=meeting_id,
    )
    minutes = await _get_minutes(session=session, meeting_id=meeting_id)
    if minutes is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="minutes not found",
        )
    return MinutesResponse(minutes=_minutes_body(minutes))


@router.post("/meetings/{meeting_id}/minutes/from-text", response_model=MinutesResponse)
async def generate_and_save_meeting_minutes(
    meeting_id: UUID,
    request: MinutesFromTextRequest | None = None,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> MinutesResponse:
    meeting = await _get_accessible_meeting(
        session=session,
        auth_user=auth_user,
        meeting_id=meeting_id,
    )
    minutes = await _generate_and_save_minutes(
        session=session,
        meeting=meeting,
        text=request.text if request else None,
    )
    return MinutesResponse(minutes=_minutes_body(minutes))


@router.post(
    "/meetings/{meeting_id}/minutes_to_slack",
    response_model=MinutesToSlackResponse,
)
async def generate_minutes_and_post_to_slack(
    meeting_id: UUID,
    request: MinutesToSlackRequest | None = None,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> MinutesToSlackResponse:
    meeting = await _get_accessible_meeting(
        session=session,
        auth_user=auth_user,
        meeting_id=meeting_id,
    )

    channel_id = ((request.channel_id if request else None) or "").strip()
    if not channel_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="channel_id is required",
        )

    minutes = await _generate_and_save_minutes(
        session=session,
        meeting=meeting,
        text=request.text if request else None,
    )

    try:
        post_log = await create_slack_post_for_minutes(
            session=session,
            team_id=meeting.team_id,
            minutes=minutes,
            channel_id=channel_id,
        )
    except SlackConnectionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="slack connection not found",
        ) from exc
    except SlackPostError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to post minutes to slack",
        ) from exc

    return MinutesToSlackResponse(
        minutes=_minutes_body(minutes),
        slack_post=_minutes_to_slack_post_body(post_log),
    )


async def _generate_and_save_minutes(
    *,
    session: AsyncSession,
    meeting: Meeting,
    text: str | None,
) -> MeetingMinutes:
    normalized_text = _validate_minutes_text(text)

    try:
        body = await run_in_threadpool(generate_minutes_from_text, normalized_text)
    except MinutesGenerationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to generate minutes",
        ) from exc

    minutes = await _get_minutes(session=session, meeting_id=meeting.id)
    if minutes is None:
        minutes = MeetingMinutes(
            meeting_id=meeting.id,
            title=DEFAULT_MINUTES_TITLE,
            body=body,
            source_text=normalized_text,
            is_deleted=False,
        )
        session.add(minutes)
    else:
        minutes.title = minutes.title or DEFAULT_MINUTES_TITLE
        minutes.body = body
        minutes.source_text = normalized_text
        minutes.is_deleted = False

    await session.commit()
    await session.refresh(minutes)
    return minutes


def _validate_minutes_text(text: str | None) -> str:
    normalized_text = (text or "").strip()
    if not normalized_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text is required",
        )
    if len(normalized_text) > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text must be 50000 characters or less",
        )
    return normalized_text


async def _get_accessible_meeting(
    session: AsyncSession,
    auth_user: AuthenticatedUser,
    meeting_id: UUID,
) -> Meeting:
    result = await session.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.is_deleted.is_(False),
        )
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="meeting not found",
        )

    await require_team_member(
        session=session,
        auth_user=auth_user,
        team_id=meeting.team_id,
    )
    return meeting


async def _get_minutes(
    session: AsyncSession,
    meeting_id: UUID,
) -> MeetingMinutes | None:
    result = await session.execute(
        select(MeetingMinutes).where(
            MeetingMinutes.meeting_id == meeting_id,
            MeetingMinutes.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


def _meeting_body(meeting: Meeting) -> MeetingBody:
    return MeetingBody(
        id=meeting.id,
        team_id=meeting.team_id,
        title=meeting.title,
        themes=meeting.themes,
        status=meeting.status,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        created_at=meeting.created_at,
        updated_at=meeting.updated_at,
        participant_count=1,
    )


def _datetime_from_milliseconds(value: int | float | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return datetime.now(timezone.utc)


def _minutes_body(minutes: MeetingMinutes) -> MinutesBody:
    return MinutesBody(
        id=minutes.id,
        meeting_id=minutes.meeting_id,
        title=minutes.title,
        body=minutes.body,
        created_at=minutes.created_at,
        updated_at=minutes.updated_at,
    )


def _minutes_to_slack_post_body(post_log: SlackPostLog) -> MinutesToSlackPostBody:
    return MinutesToSlackPostBody(
        id=post_log.id,
        minutes_id=post_log.minutes_id,
        channel_id=post_log.channel_id,
        channel_name=post_log.channel_name,
        slack_ts=post_log.slack_ts,
        status=post_log.status,
        created_at=post_log.created_at,
    )
