import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.api.tasks import sync_task_to_notion
from app.services.notion_service import NotionApiError, NotionPage


def _task():
    return SimpleNamespace(
        id=uuid4(),
        team_id=uuid4(),
        title="Implement Notion sync",
        body="Create or update the Notion page.",
        status="in_progress",
        assignee_name="Kirby",
        due_at=None,
        notion_page_id=None,
    )


def _connection(*, database_id: str | None = "database-id"):
    return SimpleNamespace(
        id=uuid4(),
        access_token="access-token",
        default_database_id=database_id,
    )


class SyncTaskToNotionTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.api.tasks.create_notion_sync_log", new_callable=AsyncMock)
    @patch("app.api.tasks.mark_task_notion_synced", new_callable=AsyncMock)
    @patch("app.api.tasks.sync_task_page", new_callable=AsyncMock)
    @patch("app.api.tasks.get_active_notion_connection", new_callable=AsyncMock)
    @patch("app.api.tasks._get_accessible_task", new_callable=AsyncMock)
    async def test_syncs_task_and_records_success(
        self,
        get_task,
        get_connection,
        sync_page,
        mark_synced,
        create_log,
    ) -> None:
        task = _task()
        connection = _connection()
        session = AsyncMock()
        get_task.return_value = task
        get_connection.return_value = connection
        sync_page.return_value = NotionPage(id="notion-page-id", url=None)

        response = await sync_task_to_notion(
            task_id=task.id,
            auth_user=SimpleNamespace(),
            session=session,
        )

        self.assertEqual(response.notion_sync.status, "success")
        self.assertEqual(response.notion_sync.task_id, task.id)
        self.assertEqual(response.notion_sync.notion_page_id, "notion-page-id")
        sync_page.assert_awaited_once_with(
            access_token="access-token",
            database_id="database-id",
            task_id=str(task.id),
            title=task.title,
            body=task.body,
            status=task.status,
            assignee_name=task.assignee_name,
            due_at=None,
            notion_page_id=None,
        )
        mark_synced.assert_awaited_once()
        self.assertEqual(mark_synced.await_args.kwargs["notion_page_id"], "notion-page-id")
        create_log.assert_awaited_once()
        self.assertEqual(create_log.await_args.kwargs["status"], "success")
        session.commit.assert_awaited_once()

    @patch("app.api.tasks.create_notion_sync_log", new_callable=AsyncMock)
    @patch("app.api.tasks.sync_task_page", new_callable=AsyncMock)
    @patch("app.api.tasks.get_active_notion_connection", new_callable=AsyncMock)
    @patch("app.api.tasks._get_accessible_task", new_callable=AsyncMock)
    async def test_records_failed_log_when_notion_rejects_sync(
        self,
        get_task,
        get_connection,
        sync_page,
        create_log,
    ) -> None:
        task = _task()
        task.notion_page_id = "existing-page-id"
        connection = _connection()
        session = AsyncMock()
        get_task.return_value = task
        get_connection.return_value = connection
        sync_page.side_effect = NotionApiError("notion validation error")

        with self.assertRaises(HTTPException) as raised:
            await sync_task_to_notion(
                task_id=task.id,
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "failed to sync task to notion")
        create_log.assert_awaited_once()
        self.assertEqual(create_log.await_args.kwargs["status"], "failed")
        self.assertEqual(create_log.await_args.kwargs["notion_page_id"], "existing-page-id")
        self.assertEqual(create_log.await_args.kwargs["error_message"], "notion validation error")
        session.commit.assert_awaited_once()

    @patch("app.api.tasks.get_active_notion_connection", new_callable=AsyncMock)
    @patch("app.api.tasks._get_accessible_task", new_callable=AsyncMock)
    async def test_requires_notion_connection(self, get_task, get_connection) -> None:
        task = _task()
        get_task.return_value = task
        get_connection.return_value = None

        with self.assertRaises(HTTPException) as raised:
            await sync_task_to_notion(
                task_id=task.id,
                auth_user=SimpleNamespace(),
                session=AsyncMock(),
            )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "notion connection not found")

    @patch("app.api.tasks.get_active_notion_connection", new_callable=AsyncMock)
    @patch("app.api.tasks._get_accessible_task", new_callable=AsyncMock)
    async def test_requires_default_database(self, get_task, get_connection) -> None:
        task = _task()
        get_task.return_value = task
        get_connection.return_value = _connection(database_id=None)

        with self.assertRaises(HTTPException) as raised:
            await sync_task_to_notion(
                task_id=task.id,
                auth_user=SimpleNamespace(),
                session=AsyncMock(),
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "notion default database is not configured")


if __name__ == "__main__":
    unittest.main()
