from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx


class AiboardConfigurationError(RuntimeError):
    pass


class AiboardRequestError(RuntimeError):
    pass


class AiboardAuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AiboardCreatedMeeting:
    id: UUID
    host_id: str | None
    team_id: str
    title: str
    themes: list[dict[str, Any]] | None
    created_at_ms: int | float | None
    payload: dict[str, Any]


async def create_aiboard_meeting(
    *,
    api_base_url: str | None,
    api_key: str | None,
    title: str,
    theme: str | None,
    host_email: str,
    team_id: UUID,
) -> AiboardCreatedMeeting:
    base_url = (api_base_url or "").strip().rstrip("/")
    if not base_url:
        raise AiboardConfigurationError("aiboard api is not configured")

    normalized_api_key = (api_key or "").strip()
    if not normalized_api_key:
        raise AiboardConfigurationError("aiboard api key is not configured")

    request_body: dict[str, str] = {
        "title": title,
        "host_email": host_email,
        "team_id": str(team_id),
    }
    if theme:
        request_body["theme"] = theme

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{base_url}/api/meetings",
                json=request_body,
                headers={"X-Api-Key": normalized_api_key},
            )
            if response.status_code in (401, 403):
                raise AiboardAuthenticationError("aiboard api key was rejected")
            response.raise_for_status()
    except AiboardAuthenticationError:
        raise
    except httpx.HTTPError as exc:
        raise AiboardRequestError("failed to create aiboard meeting") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AiboardRequestError("invalid aiboard response") from exc

    if not isinstance(payload, dict):
        raise AiboardRequestError("invalid aiboard response")

    meeting_id = _parse_uuid(payload.get("id"))
    response_title = _text(payload.get("title"))
    response_host_id = _text(payload.get("host_id")) or None
    response_team_id = _text(payload.get("team_id"))
    if meeting_id is None or not response_title or not response_team_id:
        raise AiboardRequestError("invalid aiboard response")
    if response_team_id != str(team_id):
        raise AiboardRequestError("aiboard response team does not match")

    return AiboardCreatedMeeting(
        id=meeting_id,
        host_id=response_host_id,
        team_id=response_team_id,
        title=response_title,
        themes=_themes(payload.get("themes")),
        created_at_ms=_number(payload.get("created_at")),
        payload=payload,
    )


def build_aiboard_launch_url(
    *,
    frontend_base_url: str | None,
    team_id: UUID,
    meeting_id: UUID,
) -> str:
    base_url = (frontend_base_url or "").strip().rstrip("/")
    if not base_url:
        raise AiboardConfigurationError("aiboard frontend is not configured")

    query = urlencode(
        {
            "teamId": str(team_id),
            "meetingId": str(meeting_id),
        }
    )
    return f"{base_url}/?{query}"


def _parse_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _themes(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    themes = [item for item in value if isinstance(item, dict)]
    return themes or None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
