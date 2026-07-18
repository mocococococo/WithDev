from dataclasses import dataclass

import httpx


NOTION_AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
NOTION_OAUTH_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
NOTION_SEARCH_URL = "https://api.notion.com/v1/search"
NOTION_VERSION = "2022-06-28"
NOTION_DATABASE_PAGE_SIZE = 100


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


def _notion_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
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