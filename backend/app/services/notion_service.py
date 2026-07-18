from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


NOTION_AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
NOTION_OAUTH_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
NOTION_SEARCH_URL = "https://api.notion.com/v1/search"
NOTION_PAGES_URL = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2022-06-28"
NOTION_DATABASE_PAGE_SIZE = 100
NOTION_RICH_TEXT_CHUNK_SIZE = 2_000
NOTION_RICH_TEXT_MAX_ITEMS = 100


class NotionApiError(Exception):
    pass


@dataclass(frozen=True)
class NotionOAuthToken:
    access_token: str
    notion_workspace_id: str
    notion_workspace_name: str | None
    bot_id: str | None


@dataclass(frozen=True)
class NotionDatabase:
    id: str
    title: str


@dataclass(frozen=True)
class NotionPage:
    id: str
    url: str | None


async def exchange_oauth_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> NotionOAuthToken:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            NOTION_OAUTH_TOKEN_URL,
            auth=(client_id, client_secret),
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )

    payload = _parse_notion_payload(response)
    access_token = payload.get("access_token")
    workspace_id = payload.get("workspace_id")
    if not isinstance(access_token, str) or not isinstance(workspace_id, str):
        raise NotionApiError("notion oauth response is missing token or workspace id")

    workspace_name = payload.get("workspace_name")
    bot_id = payload.get("bot_id")
    return NotionOAuthToken(
        access_token=access_token,
        notion_workspace_id=workspace_id,
        notion_workspace_name=workspace_name if isinstance(workspace_name, str) else None,
        bot_id=bot_id if isinstance(bot_id, str) else None,
    )


async def list_databases(*, access_token: str) -> list[NotionDatabase]:
    databases: list[NotionDatabase] = []
    cursor: str | None = None

    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            request_body: dict[str, object] = {
                "filter": {"property": "object", "value": "database"},
                "page_size": NOTION_DATABASE_PAGE_SIZE,
            }
            if cursor:
                request_body["start_cursor"] = cursor

            response = await client.post(
                NOTION_SEARCH_URL,
                json=request_body,
                headers=_notion_headers(access_token),
            )
            payload = _parse_notion_payload(response)
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                raise NotionApiError("notion search response is missing results")

            for raw_database in raw_results:
                if not isinstance(raw_database, dict):
                    continue
                database_id = raw_database.get("id")
                if not isinstance(database_id, str):
                    continue
                databases.append(
                    NotionDatabase(
                        id=database_id,
                        title=_extract_database_title(raw_database),
                    )
                )

            has_more = payload.get("has_more") is True
            next_cursor = payload.get("next_cursor")
            cursor = next_cursor if isinstance(next_cursor, str) and next_cursor else None
            if not has_more or cursor is None:
                return databases


async def sync_task_page(
    *,
    access_token: str,
    database_id: str,
    task_id: str,
    title: str,
    body: str,
    status: str,
    assignee_name: str | None,
    due_at: datetime | None,
    notion_page_id: str | None = None,
) -> NotionPage:
    properties = build_task_page_properties(
        task_id=task_id,
        title=title,
        body=body,
        status=status,
        assignee_name=assignee_name,
        due_at=due_at,
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if notion_page_id:
                response = await client.patch(
                    f"{NOTION_PAGES_URL}/{notion_page_id}",
                    json={"properties": properties},
                    headers=_notion_headers(access_token),
                )
            else:
                response = await client.post(
                    NOTION_PAGES_URL,
                    json={
                        "parent": {"database_id": database_id},
                        "properties": properties,
                    },
                    headers=_notion_headers(access_token),
                )
    except httpx.HTTPError as exc:
        raise NotionApiError("failed to call notion api") from exc

    payload = _parse_notion_payload(response)
    page_id = payload.get("id")
    if not isinstance(page_id, str) or not page_id:
        raise NotionApiError("notion page response is missing page id")

    page_url = payload.get("url")
    return NotionPage(
        id=page_id,
        url=page_url if isinstance(page_url, str) else None,
    )


def build_task_page_properties(
    *,
    task_id: str,
    title: str,
    body: str,
    status: str,
    assignee_name: str | None,
    due_at: datetime | None,
) -> dict[str, Any]:
    normalized_title = title.strip() or "Untitled task"
    normalized_status = status.strip() or "todo"
    normalized_assignee = (assignee_name or "").strip()

    return {
        "Name": {"title": _rich_text(normalized_title)},
        "Status": {"select": {"name": normalized_status}},
        "Assignee": {"rich_text": _rich_text(normalized_assignee)},
        "Due Date": {"date": _notion_date(due_at)},
        "Body": {"rich_text": _rich_text(body)},
        "WithDev ID": {"rich_text": _rich_text(task_id)},
    }


def _notion_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _rich_text(value: str) -> list[dict[str, Any]]:
    if not value:
        return []

    chunks = [
        value[index : index + NOTION_RICH_TEXT_CHUNK_SIZE]
        for index in range(0, len(value), NOTION_RICH_TEXT_CHUNK_SIZE)
    ]
    if len(chunks) > NOTION_RICH_TEXT_MAX_ITEMS:
        raise NotionApiError("notion rich text value exceeds limit")

    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def _notion_date(value: datetime | None) -> dict[str, str | None] | None:
    if value is None:
        return None

    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return {
        "start": normalized.isoformat(),
        "end": None,
    }


def _parse_notion_payload(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise NotionApiError("notion response is not json") from exc

    if not isinstance(payload, dict):
        raise NotionApiError("notion response is invalid")
    if not response.is_success or payload.get("object") == "error":
        message = payload.get("message")
        raise NotionApiError(message if isinstance(message, str) else "notion api error")

    return payload


def _extract_database_title(raw_database: dict) -> str:
    raw_title = raw_database.get("title")
    if not isinstance(raw_title, list):
        return "Untitled database"

    title = "".join(
        item.get("plain_text", "")
        for item in raw_title
        if isinstance(item, dict) and isinstance(item.get("plain_text"), str)
    ).strip()
    return title or "Untitled database"
