from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from app.core.auth import AuthenticatedUser, verify_firebase_user
from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.meeting import Meeting
from app.models.minutes import MeetingMinutes
from app.models.slack import (
    AiboardSlackConnection,
    AiboardSlackOAuthState,
    SlackConnection,
    SlackOAuthState,
    SlackPostLog,
)
from app.services.slack_post_service import (
    SlackConnectionNotFoundError,
    SlackPostError,
    create_slack_post_for_minutes,
    get_active_slack_connection,
    get_slack_channel_name,
)
from app.services.slack_service import (
    SLACK_AUTHORIZE_URL,
    SLACK_BOT_SCOPES,
    SlackApiError,
    exchange_oauth_code,
    list_public_channels,
)
from app.services.team_access_service import require_team_member


router = APIRouter()
settings = get_settings()
STATE_TTL_MINUTES = 10


class SlackOAuthStartResponse(BaseModel):
    url: str


class SlackChannelBody(BaseModel):
    id: str
    name: str
    is_private: bool


class SlackChannelListResponse(BaseModel):
    channels: list[SlackChannelBody]


class SlackConnectionBody(BaseModel):
    connected: bool
    slack_team_id: str | None = None
    slack_team_name: str | None = None
    default_channel_id: str | None = None
    default_channel_name: str | None = None


class SlackConnectionResponse(BaseModel):
    connection: SlackConnectionBody


class SlackDefaultChannelRequest(BaseModel):
    channel_id: str | None = None
    channel_name: str | None = None


class SlackPostRequest(BaseModel):
    channel_id: str | None = None


class SlackPostBody(BaseModel):
    id: UUID
    minutes_id: UUID
    channel_id: str
    channel_name: str | None
    slack_ts: str | None
    status: str
    created_at: datetime


class SlackPostResponse(BaseModel):
    slack_post: SlackPostBody


@router.get("/teams/{team_id}/slack/oauth/start", response_model=SlackOAuthStartResponse)
async def start_slack_oauth(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> SlackOAuthStartResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)

    if not settings.slack_client_id or not settings.slack_redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="slack oauth is not configured",
        )

    state_value = token_urlsafe(32)
    oauth_state = SlackOAuthState(
        state=state_value,
        team_id=team_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=STATE_TTL_MINUTES),
    )
    session.add(oauth_state)
    await session.commit()

    query = urlencode(
        {
            "client_id": settings.slack_client_id,
            "scope": ",".join(SLACK_BOT_SCOPES),
            "redirect_uri": settings.slack_redirect_uri,
            "state": state_value,
        }
    )
    return SlackOAuthStartResponse(url=f"{SLACK_AUTHORIZE_URL}?{query}")


@router.get("/teams/{team_id}/slack/connection", response_model=SlackConnectionResponse)
async def get_slack_connection(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> SlackConnectionResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)

    try:
        connection = await get_active_slack_connection(session=session, team_id=team_id)
    except SlackConnectionNotFoundError:
        return SlackConnectionResponse(connection=SlackConnectionBody(connected=False))

    return SlackConnectionResponse(connection=_slack_connection_body(connection))


@router.patch("/teams/{team_id}/slack/default-channel", response_model=SlackConnectionResponse)
async def update_slack_default_channel(
    team_id: UUID,
    request: SlackDefaultChannelRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> SlackConnectionResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)

    channel_id = (request.channel_id or "").strip()
    if not channel_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="channel_id is required",
        )

    try:
        connection = await get_active_slack_connection(session=session, team_id=team_id)
    except SlackConnectionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="slack connection not found",
        ) from exc

    channel_name = (request.channel_name or "").strip() or None
    if channel_name is None:
        channel_name = await get_slack_channel_name(
            bot_access_token=connection.bot_access_token,
            channel_id=channel_id,
        )

    connection.default_channel_id = channel_id
    connection.default_channel_name = channel_name
    await session.commit()
    await session.refresh(connection)

    return SlackConnectionResponse(connection=_slack_connection_body(connection))


@router.get("/slack/oauth/callback")
async def slack_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    oauth_state = await _get_oauth_state(session=session, state=state)
    aiboard_oauth_state = None
    if oauth_state is None:
        aiboard_oauth_state = await _get_aiboard_oauth_state(session=session, state=state)

    if aiboard_oauth_state is not None:
        return await _handle_aiboard_slack_oauth_callback(
            session=session,
            oauth_state=aiboard_oauth_state,
            code=code,
            error=error,
        )

    team_id = oauth_state.team_id if oauth_state is not None else None
    if error:
        return _redirect_to_frontend("/slack/error", team_id=team_id, reason=error)
    if not code or oauth_state is None:
        return _redirect_to_frontend("/slack/error", team_id=team_id, reason="invalid_state")
    if oauth_state.consumed_at is not None:
        return _redirect_to_frontend("/slack/error", team_id=team_id, reason="state_consumed")
    if _is_expired(oauth_state.expires_at):
        return _redirect_to_frontend("/slack/error", team_id=team_id, reason="state_expired")
    if not settings.slack_client_id or not settings.slack_client_secret or not settings.slack_redirect_uri:
        return _redirect_to_frontend("/slack/error", team_id=team_id, reason="not_configured")

    try:
        token = await exchange_oauth_code(
            client_id=settings.slack_client_id,
            client_secret=settings.slack_client_secret,
            code=code,
            redirect_uri=settings.slack_redirect_uri,
        )
    except SlackApiError:
        return _redirect_to_frontend("/slack/error", team_id=team_id, reason="oauth_failed")

    await _save_slack_connection(
        session=session,
        team_id=oauth_state.team_id,
        slack_team_id=token.slack_team_id,
        slack_team_name=token.slack_team_name,
        bot_user_id=token.bot_user_id,
        bot_access_token=token.access_token,
    )
    oauth_state.consumed_at = datetime.now(timezone.utc)
    await session.commit()

    return _redirect_to_frontend("/slack/success", team_id=oauth_state.team_id)


@router.get("/teams/{team_id}/slack/channels", response_model=SlackChannelListResponse)
async def list_slack_channels(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> SlackChannelListResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)
    try:
        connection = await get_active_slack_connection(session=session, team_id=team_id)
    except SlackConnectionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="slack connection not found",
        ) from exc

    try:
        channels = await list_public_channels(bot_access_token=connection.bot_access_token)
    except SlackApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to fetch slack channels",
        ) from exc

    return SlackChannelListResponse(
        channels=[
            SlackChannelBody(id=channel.id, name=channel.name, is_private=channel.is_private)
            for channel in channels
        ]
    )


@router.post("/minutes/{minutes_id}/slack-posts", response_model=SlackPostResponse)
async def post_minutes_to_slack(
    minutes_id: UUID,
    request: SlackPostRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> SlackPostResponse:
    channel_id = (request.channel_id or "").strip()
    if not channel_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="channel_id is required",
        )

    minutes, meeting = await _get_accessible_minutes(
        session=session,
        auth_user=auth_user,
        minutes_id=minutes_id,
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

    return SlackPostResponse(slack_post=_slack_post_body(post_log))


async def _get_oauth_state(
    *,
    session: AsyncSession,
    state: str | None,
) -> SlackOAuthState | None:
    if not state:
        return None
    result = await session.execute(select(SlackOAuthState).where(SlackOAuthState.state == state))
    return result.scalar_one_or_none()


async def _get_aiboard_oauth_state(
    *,
    session: AsyncSession,
    state: str | None,
) -> AiboardSlackOAuthState | None:
    if not state:
        return None
    result = await session.execute(
        select(AiboardSlackOAuthState).where(AiboardSlackOAuthState.state == state)
    )
    return result.scalar_one_or_none()


async def _handle_aiboard_slack_oauth_callback(
    *,
    session: AsyncSession,
    oauth_state: AiboardSlackOAuthState,
    code: str | None,
    error: str | None,
) -> RedirectResponse:
    meeting_id = oauth_state.aiboard_meeting_id

    if error:
        return _redirect_to_frontend(
            "/slack/aiboard/error",
            aiboard_meeting_id=meeting_id,
            reason=error,
        )
    if not code:
        return _redirect_to_frontend(
            "/slack/aiboard/error",
            aiboard_meeting_id=meeting_id,
            reason="invalid_state",
        )
    if oauth_state.consumed_at is not None:
        return _redirect_to_frontend(
            "/slack/aiboard/error",
            aiboard_meeting_id=meeting_id,
            reason="state_consumed",
        )
    if _is_expired(oauth_state.expires_at):
        return _redirect_to_frontend(
            "/slack/aiboard/error",
            aiboard_meeting_id=meeting_id,
            reason="state_expired",
        )
    if not settings.slack_client_id or not settings.slack_client_secret or not settings.slack_redirect_uri:
        return _redirect_to_frontend(
            "/slack/aiboard/error",
            aiboard_meeting_id=meeting_id,
            reason="not_configured",
        )

    try:
        token = await exchange_oauth_code(
            client_id=settings.slack_client_id,
            client_secret=settings.slack_client_secret,
            code=code,
            redirect_uri=settings.slack_redirect_uri,
        )
    except SlackApiError:
        return _redirect_to_frontend(
            "/slack/aiboard/error",
            aiboard_meeting_id=meeting_id,
            reason="oauth_failed",
        )

    await _save_aiboard_slack_connection(
        session=session,
        meeting_id=meeting_id,
        slack_team_id=token.slack_team_id,
        slack_team_name=token.slack_team_name,
        bot_user_id=token.bot_user_id,
        bot_access_token=token.access_token,
    )
    oauth_state.consumed_at = datetime.now(timezone.utc)
    await session.commit()

    return _redirect_to_frontend("/slack/aiboard/success", aiboard_meeting_id=meeting_id)


async def _save_slack_connection(
    *,
    session: AsyncSession,
    team_id: UUID,
    slack_team_id: str,
    slack_team_name: str | None,
    bot_user_id: str | None,
    bot_access_token: str,
) -> SlackConnection:
    result = await session.execute(
        select(SlackConnection).where(
            SlackConnection.team_id == team_id,
            SlackConnection.is_deleted.is_(False),
        )
    )
    existing: SlackConnection | None = None
    for connection in result.scalars().all():
        if connection.slack_team_id == slack_team_id:
            existing = connection
        else:
            connection.status = "revoked"
            connection.is_deleted = True

    if existing is None:
        existing = SlackConnection(
            team_id=team_id,
            slack_team_id=slack_team_id,
            slack_team_name=slack_team_name,
            bot_user_id=bot_user_id,
            bot_access_token=bot_access_token,
            status="active",
            is_deleted=False,
        )
        session.add(existing)
    else:
        existing.slack_team_name = slack_team_name
        existing.bot_user_id = bot_user_id
        existing.bot_access_token = bot_access_token
        existing.status = "active"
        existing.is_deleted = False

    return existing


async def _save_aiboard_slack_connection(
    *,
    session: AsyncSession,
    meeting_id: UUID,
    slack_team_id: str,
    slack_team_name: str | None,
    bot_user_id: str | None,
    bot_access_token: str,
) -> AiboardSlackConnection:
    result = await session.execute(
        select(AiboardSlackConnection).where(
            AiboardSlackConnection.aiboard_meeting_id == meeting_id,
            AiboardSlackConnection.is_deleted.is_(False),
        )
    )
    existing: AiboardSlackConnection | None = None
    for connection in result.scalars().all():
        if connection.slack_team_id == slack_team_id:
            existing = connection
        else:
            connection.status = "revoked"
            connection.is_deleted = True

    if existing is None:
        existing = AiboardSlackConnection(
            aiboard_meeting_id=meeting_id,
            slack_team_id=slack_team_id,
            slack_team_name=slack_team_name,
            bot_user_id=bot_user_id,
            bot_access_token=bot_access_token,
            status="active",
            is_deleted=False,
        )
        session.add(existing)
    else:
        existing.slack_team_name = slack_team_name
        existing.bot_user_id = bot_user_id
        existing.bot_access_token = bot_access_token
        existing.status = "active"
        existing.is_deleted = False

    return existing


async def _get_accessible_minutes(
    *,
    session: AsyncSession,
    auth_user: AuthenticatedUser,
    minutes_id: UUID,
) -> tuple[MeetingMinutes, Meeting]:
    minutes_result = await session.execute(
        select(MeetingMinutes).where(
            MeetingMinutes.id == minutes_id,
            MeetingMinutes.is_deleted.is_(False),
        )
    )
    minutes = minutes_result.scalar_one_or_none()
    if minutes is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="minutes not found",
        )

    meeting_result = await session.execute(
        select(Meeting).where(
            Meeting.id == minutes.meeting_id,
            Meeting.is_deleted.is_(False),
        )
    )
    meeting = meeting_result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="meeting not found",
        )

    await require_team_member(session=session, auth_user=auth_user, team_id=meeting.team_id)
    return minutes, meeting


def _redirect_to_frontend(
    path: str,
    *,
    team_id: UUID | None = None,
    aiboard_meeting_id: UUID | None = None,
    reason: str | None = None,
) -> RedirectResponse:
    params: dict[str, str] = {}
    if team_id is not None:
        params["team_id"] = str(team_id)
    if aiboard_meeting_id is not None:
        params["meeting_id"] = str(aiboard_meeting_id)
    if reason:
        params["reason"] = reason

    base_url = settings.frontend_base_url.rstrip("/")
    query = urlencode(params)
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{query}"
    return RedirectResponse(url=url)


def _is_expired(value: datetime) -> bool:
    expires_at = value
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def _slack_post_body(post_log: SlackPostLog) -> SlackPostBody:
    return SlackPostBody(
        id=post_log.id,
        minutes_id=post_log.minutes_id,
        channel_id=post_log.channel_id,
        channel_name=post_log.channel_name,
        slack_ts=post_log.slack_ts,
        status=post_log.status,
        created_at=post_log.created_at,
    )


def _slack_connection_body(connection: SlackConnection) -> SlackConnectionBody:
    return SlackConnectionBody(
        connected=True,
        slack_team_id=connection.slack_team_id,
        slack_team_name=connection.slack_team_name,
        default_channel_id=connection.default_channel_id,
        default_channel_name=connection.default_channel_name,
    )
