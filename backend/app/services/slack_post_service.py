from urllib.parse import quote
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.minutes import MeetingMinutes
from app.models.slack import SlackConnection, SlackPostLog
from app.services.slack_service import (
    SlackApiError,
    build_minutes_markdown,
    build_minutes_markdown_filename,
    list_public_channels,
    upload_markdown_file,
)


class SlackConnectionNotFoundError(Exception):
    pass


class SlackPostError(Exception):
    pass


def build_team_tasks_url(*, frontend_base_url: str, team_id: UUID) -> str:
    base_url = frontend_base_url.rstrip("/")
    return f"{base_url}/teams/{quote(str(team_id), safe='')}/tasks"


def build_minutes_upload_comment(*, frontend_base_url: str, team_id: UUID) -> str:
    tasks_url = build_team_tasks_url(
        frontend_base_url=frontend_base_url,
        team_id=team_id,
    )
    return f"議事録を共有します。\nタスク一覧: {tasks_url}"


async def get_active_slack_connection(
    *,
    session: AsyncSession,
    team_id: UUID,
) -> SlackConnection:
    result = await session.execute(
        select(SlackConnection)
        .where(
            SlackConnection.team_id == team_id,
            SlackConnection.status == "active",
            SlackConnection.is_deleted.is_(False),
        )
        .order_by(SlackConnection.updated_at.desc())
        .limit(1)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise SlackConnectionNotFoundError("slack connection not found")
    return connection


async def get_slack_channel_name(
    *,
    bot_access_token: str,
    channel_id: str,
) -> str | None:
    try:
        channels = await list_public_channels(bot_access_token=bot_access_token)
    except SlackApiError:
        return None

    for channel in channels:
        if channel.id == channel_id:
            return channel.name
    return None


async def create_slack_post_for_minutes(
    *,
    session: AsyncSession,
    team_id: UUID,
    minutes: MeetingMinutes,
    channel_id: str,
    frontend_base_url: str,
    document_title: str | None = None,
) -> SlackPostLog:
    connection = await get_active_slack_connection(session=session, team_id=team_id)
    channel_name = await get_slack_channel_name(
        bot_access_token=connection.bot_access_token,
        channel_id=channel_id,
    )

    try:
        normalized_title = (document_title or minutes.title or "").strip() or "議事録"
        upload_result = await upload_markdown_file(
            bot_access_token=connection.bot_access_token,
            channel_id=channel_id,
            filename=build_minutes_markdown_filename(title=normalized_title),
            title=normalized_title,
            content=build_minutes_markdown(title=normalized_title, body=minutes.body),
            initial_comment=build_minutes_upload_comment(
                frontend_base_url=frontend_base_url,
                team_id=team_id,
            ),
        )
    except SlackApiError as exc:
        failed_log = SlackPostLog(
            minutes_id=minutes.id,
            slack_connection_id=connection.id,
            channel_id=channel_id,
            channel_name=channel_name,
            slack_ts=None,
            status="failed",
            error_message=str(exc)[:1000],
            is_deleted=False,
        )
        session.add(failed_log)
        await session.commit()
        error_message = (
            "slack reconnect required"
            if str(exc) == "missing_scope"
            else "failed to post minutes to slack"
        )
        raise SlackPostError(error_message) from exc

    post_log = SlackPostLog(
        minutes_id=minutes.id,
        slack_connection_id=connection.id,
        channel_id=upload_result.channel_id,
        channel_name=channel_name,
        slack_ts=None,
        status="success",
        error_message=None,
        is_deleted=False,
    )
    session.add(post_log)
    await session.commit()
    await session.refresh(post_log)
    return post_log
