import re
from dataclasses import dataclass

import httpx


SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"
SLACK_CONVERSATIONS_LIST_URL = "https://slack.com/api/conversations.list"
SLACK_CONVERSATIONS_JOIN_URL = "https://slack.com/api/conversations.join"
SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_GET_UPLOAD_URL_EXTERNAL_URL = "https://slack.com/api/files.getUploadURLExternal"
SLACK_COMPLETE_UPLOAD_EXTERNAL_URL = "https://slack.com/api/files.completeUploadExternal"

SLACK_BOT_SCOPES = (
    "chat:write",
    "channels:read",
    "channels:join",
    "chat:write.public",
    "files:write",
)
SLACK_MESSAGE_LIMIT = 39000
MARKDOWN_FILENAME_MAX_STEM_LENGTH = 120


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


@dataclass(frozen=True)
class SlackFileUploadResult:
    channel_id: str
    file_id: str


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


async def upload_markdown_file(
    *,
    bot_access_token: str,
    channel_id: str,
    filename: str,
    title: str,
    content: str,
    initial_comment: str | None = None,
) -> SlackFileUploadResult:
    file_bytes = content.encode("utf-8")
    authorization_headers = {"Authorization": f"Bearer {bot_access_token}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        join_response = await client.post(
            SLACK_CONVERSATIONS_JOIN_URL,
            json={"channel": channel_id},
            headers=authorization_headers,
        )
        _parse_slack_payload(join_response)

        upload_url_response = await client.post(
            SLACK_GET_UPLOAD_URL_EXTERNAL_URL,
            data={
                "filename": filename,
                "length": len(file_bytes),
            },
            headers=authorization_headers,
        )
        upload_url_payload = _parse_slack_payload(upload_url_response)
        upload_url = upload_url_payload.get("upload_url")
        file_id = upload_url_payload.get("file_id")
        if not isinstance(upload_url, str) or not upload_url:
            raise SlackApiError("slack upload url response is missing upload_url")
        if not isinstance(file_id, str) or not file_id:
            raise SlackApiError("slack upload url response is missing file_id")

        file_response = await client.post(
            upload_url,
            content=file_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
        if not file_response.is_success:
            raise SlackApiError(f"slack file upload failed: {file_response.status_code}")

        complete_request: dict[str, object] = {
            "files": [{"id": file_id, "title": title}],
            "channel_id": channel_id,
        }
        if initial_comment:
            complete_request["initial_comment"] = initial_comment
        complete_response = await client.post(
            SLACK_COMPLETE_UPLOAD_EXTERNAL_URL,
            json=complete_request,
            headers=authorization_headers,
        )
        complete_payload = _parse_slack_payload(complete_response)

    files = complete_payload.get("files")
    if not isinstance(files, list) or not any(
        isinstance(file, dict) and file.get("id") == file_id for file in files
    ):
        raise SlackApiError("slack complete upload response is missing file")

    return SlackFileUploadResult(channel_id=channel_id, file_id=file_id)


def build_minutes_message(*, title: str | None, body: str) -> str:
    minutes_title = title or "議事録"
    return f"*{minutes_title}*\n{body}"


def build_minutes_markdown(*, title: str | None, body: str) -> str:
    minutes_title = _normalize_minutes_title(title)
    return f"# {minutes_title}\n\n{body.strip()}\n"


def build_minutes_markdown_filename(*, title: str | None) -> str:
    minutes_title = _normalize_minutes_title(title)
    if minutes_title.lower().endswith(".md"):
        minutes_title = minutes_title[:-3]
    safe_stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", minutes_title)
    safe_stem = safe_stem.strip(" .")[:MARKDOWN_FILENAME_MAX_STEM_LENGTH].rstrip(" .")
    return f"{safe_stem or '議事録'}.md"


def _normalize_minutes_title(title: str | None) -> str:
    normalized_title = " ".join((title or "").split())
    return normalized_title or "議事録"


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
