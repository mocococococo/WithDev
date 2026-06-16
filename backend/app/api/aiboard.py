from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.auth import AiboardServiceAccount, verify_aiboard_service_account
from app.db.session import get_db_session
from app.models.meeting import Meeting
from app.models.minutes import MeetingMinutes
from app.models.slack import SlackPostLog
from app.services.minutes_service import (
    MAX_TEXT_LENGTH,
    MinutesGenerationError,
    generate_minutes_from_text,
)
from app.services.slack_post_service import (
    SlackConnectionNotFoundError,
    get_active_slack_connection,
    get_slack_channel_name,
)
from app.services.slack_service import SlackApiError, build_minutes_message, post_message


router = APIRouter()
DEFAULT_MINUTES_TITLE = "議事録"


class AiboardMeetingFinishRequest(BaseModel):
    team_id: UUID
    meeting: dict[str, Any]


class AiboardMeetingBody(BaseModel):
    id: UUID
    team_id: UUID
    title: str
    theme: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AiboardMinutesBody(BaseModel):
    id: UUID
    meeting_id: UUID
    title: str | None
    body: str
    created_at: datetime
    updated_at: datetime


class AiboardSlackPostBody(BaseModel):
    id: UUID | None = None
    minutes_id: UUID | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    slack_ts: str | None = None
    status: str
    error_message: str | None = None
    created_at: datetime | None = None


class AiboardMeetingFinishResponse(BaseModel):
    meeting: AiboardMeetingBody
    minutes: AiboardMinutesBody
    slack_post: AiboardSlackPostBody


@router.post("/meetings/finish", response_model=AiboardMeetingFinishResponse)
async def finish_aiboard_meeting(
    request: AiboardMeetingFinishRequest,
    _caller: AiboardServiceAccount = Depends(verify_aiboard_service_account),
    session: AsyncSession = Depends(get_db_session),
) -> AiboardMeetingFinishResponse:
    meeting = await _upsert_aiboard_meeting(
        session=session,
        team_id=request.team_id,
        payload=request.meeting,
    )

    source_text = _build_minutes_source_text(request.meeting)
    if len(source_text) > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="aiboard meeting text must be 50000 characters or less",
        )

    minutes = await _generate_and_save_minutes(
        session=session,
        meeting=meeting,
        source_text=source_text,
    )
    slack_post = await _post_minutes_to_default_channel(
        session=session,
        team_id=request.team_id,
        minutes=minutes,
    )

    return AiboardMeetingFinishResponse(
        meeting=_meeting_body(meeting),
        minutes=_minutes_body(minutes),
        slack_post=slack_post,
    )


async def _upsert_aiboard_meeting(
    *,
    session: AsyncSession,
    team_id: UUID,
    payload: dict[str, Any],
) -> Meeting:
    meeting_id = _parse_uuid(payload.get("id"), "meeting.id is required")
    title = _normalize_text(payload.get("title")) or "Aiboard meeting"
    started_at = _parse_datetime(payload.get("created_at")) or datetime.now(timezone.utc)
    ended_at = _parse_datetime(payload.get("ended_at")) or datetime.now(timezone.utc)
    theme = _current_theme_title(payload)

    result = await session.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if meeting is not None and meeting.team_id != team_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="meeting id already belongs to another team",
        )

    if meeting is None:
        meeting = Meeting(
            id=meeting_id,
            team_id=team_id,
            title=title,
            theme=theme,
            status="ended",
            started_at=started_at,
            ended_at=ended_at,
            aiboard_payload=payload,
            is_deleted=False,
        )
        session.add(meeting)
    else:
        meeting.title = title
        meeting.theme = theme
        meeting.status = "ended"
        meeting.started_at = started_at
        meeting.ended_at = ended_at
        meeting.aiboard_payload = payload
        meeting.is_deleted = False

    await session.commit()
    await session.refresh(meeting)
    return meeting


async def _generate_and_save_minutes(
    *,
    session: AsyncSession,
    meeting: Meeting,
    source_text: str,
) -> MeetingMinutes:
    try:
        body = await run_in_threadpool(generate_minutes_from_text, source_text)
    except MinutesGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to generate minutes",
        ) from exc

    result = await session.execute(
        select(MeetingMinutes).where(MeetingMinutes.meeting_id == meeting.id)
    )
    minutes = result.scalar_one_or_none()
    if minutes is None:
        minutes = MeetingMinutes(
            meeting_id=meeting.id,
            title=DEFAULT_MINUTES_TITLE,
            body=body,
            source_text=source_text,
            is_deleted=False,
        )
        session.add(minutes)
    else:
        minutes.title = minutes.title or DEFAULT_MINUTES_TITLE
        minutes.body = body
        minutes.source_text = source_text
        minutes.is_deleted = False

    await session.commit()
    await session.refresh(minutes)
    return minutes


async def _post_minutes_to_default_channel(
    *,
    session: AsyncSession,
    team_id: UUID,
    minutes: MeetingMinutes,
) -> AiboardSlackPostBody:
    try:
        connection = await get_active_slack_connection(session=session, team_id=team_id)
    except SlackConnectionNotFoundError:
        return AiboardSlackPostBody(
            status="failed",
            error_message="slack connection not found",
        )

    channel_id = (connection.default_channel_id or "").strip()
    if not channel_id:
        return AiboardSlackPostBody(
            status="failed",
            error_message="slack default channel is not configured",
        )

    channel_name = connection.default_channel_name or await get_slack_channel_name(
        bot_access_token=connection.bot_access_token,
        channel_id=channel_id,
    )

    try:
        post_result = await post_message(
            bot_access_token=connection.bot_access_token,
            channel_id=channel_id,
            text=build_minutes_message(title=minutes.title, body=minutes.body),
        )
    except SlackApiError as exc:
        post_log = SlackPostLog(
            minutes_id=minutes.id,
            slack_connection_id=connection.id,
            channel_id=channel_id,
            channel_name=channel_name,
            slack_ts=None,
            status="failed",
            error_message=str(exc)[:1000],
            is_deleted=False,
        )
        session.add(post_log)
        await session.commit()
        await session.refresh(post_log)
        return _slack_post_body(post_log)

    post_log = SlackPostLog(
        minutes_id=minutes.id,
        slack_connection_id=connection.id,
        channel_id=post_result.channel_id,
        channel_name=channel_name,
        slack_ts=post_result.slack_ts,
        status="success",
        error_message=None,
        is_deleted=False,
    )
    session.add(post_log)
    await session.commit()
    await session.refresh(post_log)
    return _slack_post_body(post_log)


def _build_minutes_source_text(payload: dict[str, Any]) -> str:
    title = _normalize_text(payload.get("title"))
    lines: list[str] = []
    if title:
        lines.extend([f"ミーティングタイトル: {title}", ""])

    themes = payload.get("themes")
    if isinstance(themes, list):
        for theme in themes:
            if not isinstance(theme, dict):
                continue
            theme_title = _normalize_text(theme.get("title")) or "テーマ"
            content_lines = _theme_content_lines(theme)
            if not content_lines:
                continue
            lines.append(f"## {theme_title}")
            lines.extend(content_lines)
            lines.append("")

    text = "\n".join(lines).strip()
    return text or title or "Aiboard meeting"


def _theme_content_lines(theme: dict[str, Any]) -> list[str]:
    logs = theme.get("logs")
    if not isinstance(logs, list):
        return []

    lines: list[str] = []
    for log in logs:
        if not isinstance(log, dict):
            continue
        content = _normalize_text(log.get("content"))
        if content:
            lines.append(content)
    return lines


def _current_theme_title(payload: dict[str, Any]) -> str | None:
    themes = payload.get("themes")
    if not isinstance(themes, list):
        return None

    current_theme_id = payload.get("current_theme_id")
    first_title: str | None = None
    for theme in themes:
        if not isinstance(theme, dict):
            continue
        title = _normalize_text(theme.get("title"))
        if first_title is None:
            first_title = title
        if current_theme_id and theme.get("id") == current_theme_id:
            return title
    return first_title


def _parse_uuid(value: Any, error_detail: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            pass
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _normalize_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _meeting_body(meeting: Meeting) -> AiboardMeetingBody:
    return AiboardMeetingBody(
        id=meeting.id,
        team_id=meeting.team_id,
        title=meeting.title,
        theme=meeting.theme,
        status=meeting.status,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        created_at=meeting.created_at,
        updated_at=meeting.updated_at,
    )


def _minutes_body(minutes: MeetingMinutes) -> AiboardMinutesBody:
    return AiboardMinutesBody(
        id=minutes.id,
        meeting_id=minutes.meeting_id,
        title=minutes.title,
        body=minutes.body,
        created_at=minutes.created_at,
        updated_at=minutes.updated_at,
    )


def _slack_post_body(post_log: SlackPostLog) -> AiboardSlackPostBody:
    return AiboardSlackPostBody(
        id=post_log.id,
        minutes_id=post_log.minutes_id,
        channel_id=post_log.channel_id,
        channel_name=post_log.channel_name,
        slack_ts=post_log.slack_ts,
        status=post_log.status,
        error_message=post_log.error_message,
        created_at=post_log.created_at,
    )
