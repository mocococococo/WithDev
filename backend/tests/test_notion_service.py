import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx

from app.services.notion_service import (
    NOTION_PAGES_URL,
    NotionApiError,
    build_task_page_properties,
    sync_task_page,
)


class BuildTaskPagePropertiesTests(unittest.TestCase):
    def test_builds_expected_notion_properties(self) -> None:
        due_at = datetime(2026, 7, 20, 9, 30, tzinfo=timezone.utc)

        properties = build_task_page_properties(
            task_id="task-id",
            title="Implement Notion sync",
            body="Create or update the Notion page.",
            status="in_progress",
            assignee_name="Kirby",
            due_at=due_at,
        )

        self.assertEqual(
            properties["Name"]["title"][0]["text"]["content"],
            "Implement Notion sync",
        )
        self.assertEqual(properties["Status"], {"select": {"name": "in_progress"}})
        self.assertEqual(
            properties["Assignee"]["rich_text"][0]["text"]["content"],
            "Kirby",
        )
        self.assertEqual(
            properties["Due Date"]["date"]["start"],
            "2026-07-20T09:30:00+00:00",
        )
        self.assertEqual(
            properties["WithDev ID"]["rich_text"][0]["text"]["content"],
            "task-id",
        )

    def test_empty_optional_values_clear_notion_properties(self) -> None:
        properties = build_task_page_properties(
            task_id="task-id",
            title="Task",
            body="",
            status="todo",
            assignee_name=None,
            due_at=None,
        )

        self.assertEqual(properties["Assignee"], {"rich_text": []})
        self.assertEqual(properties["Due Date"], {"date": None})
        self.assertEqual(properties["Body"], {"rich_text": []})

    def test_splits_long_rich_text(self) -> None:
        properties = build_task_page_properties(
            task_id="task-id",
            title="Task",
            body="a" * 2_001,
            status="todo",
            assignee_name=None,
            due_at=None,
        )

        body_items = properties["Body"]["rich_text"]
        self.assertEqual(len(body_items), 2)
        self.assertEqual(len(body_items[0]["text"]["content"]), 2_000)
        self.assertEqual(body_items[1]["text"]["content"], "a")

    def test_rejects_rich_text_over_notion_limit(self) -> None:
        with self.assertRaises(NotionApiError):
            build_task_page_properties(
                task_id="task-id",
                title="Task",
                body="a" * 200_001,
                status="todo",
                assignee_name=None,
                due_at=None,
            )


class SyncTaskPageTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.notion_service.httpx.AsyncClient")
    async def test_creates_page_when_page_id_is_missing(self, client_class) -> None:
        client = AsyncMock()
        client.post.return_value = httpx.Response(
            200,
            json={"object": "page", "id": "new-page-id", "url": "https://notion.so/page"},
        )
        client_class.return_value.__aenter__.return_value = client

        result = await sync_task_page(
            access_token="access-token",
            database_id="database-id",
            task_id="task-id",
            title="Task",
            body="Body",
            status="todo",
            assignee_name=None,
            due_at=None,
        )

        self.assertEqual(result.id, "new-page-id")
        client.post.assert_awaited_once()
        request_url = client.post.await_args.args[0]
        request_body = client.post.await_args.kwargs["json"]
        self.assertEqual(request_url, NOTION_PAGES_URL)
        self.assertEqual(request_body["parent"], {"database_id": "database-id"})

    @patch("app.services.notion_service.httpx.AsyncClient")
    async def test_updates_page_when_page_id_exists(self, client_class) -> None:
        client = AsyncMock()
        client.patch.return_value = httpx.Response(
            200,
            json={"object": "page", "id": "page-id"},
        )
        client_class.return_value.__aenter__.return_value = client

        result = await sync_task_page(
            access_token="access-token",
            database_id="database-id",
            task_id="task-id",
            title="Task",
            body="Body",
            status="done",
            assignee_name="Kirby",
            due_at=None,
            notion_page_id="page-id",
        )

        self.assertEqual(result.id, "page-id")
        client.patch.assert_awaited_once()
        request_url = client.patch.await_args.args[0]
        request_body = client.patch.await_args.kwargs["json"]
        self.assertEqual(request_url, f"{NOTION_PAGES_URL}/page-id")
        self.assertNotIn("parent", request_body)

    @patch("app.services.notion_service.httpx.AsyncClient")
    async def test_wraps_http_errors(self, client_class) -> None:
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectError("connection failed")
        client_class.return_value.__aenter__.return_value = client

        with self.assertRaisesRegex(NotionApiError, "failed to call notion api"):
            await sync_task_page(
                access_token="access-token",
                database_id="database-id",
                task_id="task-id",
                title="Task",
                body="Body",
                status="todo",
                assignee_name=None,
                due_at=None,
            )


if __name__ == "__main__":
    unittest.main()
