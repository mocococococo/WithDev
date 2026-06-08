from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.minutes import MeetingMinutes
from app.models.slack import SlackConnection, SlackPostLog
from app.services.slack_service import (
    SlackApiError,
    build_minutes_message,
    list_public_channels,
    post_message,
)


class SlackConnectionNotFoundError(Exception):
    pass


class SlackPostError(Exception):
    pass


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
) -> SlackPostLog:
    connection = await get_active_slack_connection(session=session, team_id=team_id)
    channel_name = await get_slack_channel_name(
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
        raise SlackPostError("failed to post minutes to slack") from exc

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
    return post_log
