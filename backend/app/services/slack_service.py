from dataclasses import dataclass

import httpx


SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"
SLACK_CONVERSATIONS_LIST_URL = "https://slack.com/api/conversations.list"
SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"

SLACK_BOT_SCOPES = ("chat:write", "channels:read", "chat:write.public")
SLACK_MESSAGE_LIMIT = 39000


class SlackApiError(Exception):
    pass


@dataclass(frozen=True)
class SlackOAuthToken:
    access_token: str
    slack_team_id: str
    slack_team_name: str | None
    bot_user_id: str | None


@dataclass(frozen=True)
class SlackChannel:
    id: str
    name: str
    is_private: bool


@dataclass(frozen=True)
class SlackPostResult:
    channel_id: str
    slack_ts: str


async def exchange_oauth_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> SlackOAuthToken:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            SLACK_OAUTH_ACCESS_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )

    payload = _parse_slack_payload(response)
    access_token = payload.get("access_token")
    team = payload.get("team")
    if not isinstance(access_token, str) or not isinstance(team, dict):
        raise SlackApiError("slack oauth response is missing token or team")

    slack_team_id = team.get("id")
    if not isinstance(slack_team_id, str):
        raise SlackApiError("slack oauth response is missing team id")

    slack_team_name = team.get("name")
    bot_user_id = payload.get("bot_user_id")
    return SlackOAuthToken(
        access_token=access_token,
        slack_team_id=slack_team_id,
        slack_team_name=slack_team_name if isinstance(slack_team_name, str) else None,
        bot_user_id=bot_user_id if isinstance(bot_user_id, str) else None,
    )


async def list_public_channels(*, bot_access_token: str) -> list[SlackChannel]:
    channels: list[SlackChannel] = []
    cursor: str | None = None

    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            params = {
                "types": "public_channel",
                "exclude_archived": "true",
                "limit": "200",
            }
            if cursor:
                params["cursor"] = cursor

            response = await client.get(
                SLACK_CONVERSATIONS_LIST_URL,
                params=params,
                headers={"Authorization": f"Bearer {bot_access_token}"},
            )
            payload = _parse_slack_payload(response)

            raw_channels = payload.get("channels")
            if not isinstance(raw_channels, list):
                raise SlackApiError("slack conversations response is missing channels")

            for raw_channel in raw_channels:
                if not isinstance(raw_channel, dict):
                    continue
                channel_id = raw_channel.get("id")
                channel_name = raw_channel.get("name")
                if not isinstance(channel_id, str) or not isinstance(channel_name, str):
                    continue
                channels.append(
                    SlackChannel(
                        id=channel_id,
                        name=channel_name,
                        is_private=bool(raw_channel.get("is_private")),
                    )
                )

            metadata = payload.get("response_metadata")
            next_cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
            cursor = next_cursor if isinstance(next_cursor, str) and next_cursor else None
            if not cursor:
                return channels


async def post_message(
    *,
    bot_access_token: str,
    channel_id: str,
    text: str,
) -> SlackPostResult:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            SLACK_POST_MESSAGE_URL,
            json={
                "channel": channel_id,
                "text": _truncate_message(text),
            },
            headers={"Authorization": f"Bearer {bot_access_token}"},
        )

    payload = _parse_slack_payload(response)
    slack_ts = payload.get("ts")
    channel = payload.get("channel")
    if not isinstance(slack_ts, str):
        raise SlackApiError("slack post response is missing timestamp")

    return SlackPostResult(
        channel_id=channel if isinstance(channel, str) else channel_id,
        slack_ts=slack_ts,
    )


def build_minutes_message(*, title: str | None, body: str) -> str:
    minutes_title = title or "議事録"
    return f"*{minutes_title}*\n{body}"


def _parse_slack_payload(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SlackApiError("slack response is not json") from exc

    if not isinstance(payload, dict):
        raise SlackApiError("slack response is invalid")
    if not response.is_success:
        raise SlackApiError(f"slack http error: {response.status_code}")
    if payload.get("ok") is not True:
        error = payload.get("error")
        raise SlackApiError(error if isinstance(error, str) else "slack api error")

    return payload


def _truncate_message(text: str) -> str:
    if len(text) <= SLACK_MESSAGE_LIMIT:
        return text
    return f"{text[:SLACK_MESSAGE_LIMIT]}\n\n...(truncated)"
