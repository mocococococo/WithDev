import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.api.tasks import (
    TaskGenerateRequest,
    generate_team_tasks,
    list_minutes_tasks,
)
from app.models.task import Task, TaskMinutesImpact


def make_task(*, team_id, source_minutes_id=None) -> Task:
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid4(),
        team_id=team_id,
        source_minutes_id=source_minutes_id,
        title="確認する",
        body="確認内容",
        assignee_user_id=None,
        assignee_name=None,
        status="todo",
        due_at=None,
        is_deleted=False,
    )
    task.created_at = now
    task.updated_at = now
    return task


class TaskMinutesImpactGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ignores_ai_delete_action(self) -> None:
        team_id = uuid4()
        minutes_id = uuid4()
        task = make_task(team_id=team_id, source_minutes_id=uuid4())
        session = MagicMock()
        session.commit = AsyncMock()

        with (
            patch("app.api.tasks.require_team_member", new_callable=AsyncMock),
            patch(
                "app.api.tasks._get_accessible_minutes",
                new_callable=AsyncMock,
                return_value=(
                    SimpleNamespace(id=minutes_id, body="議事録"),
                    SimpleNamespace(aiboard_payload={}),
                ),
            ),
            patch(
                "app.api.tasks._get_active_team_members",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.api.tasks._get_team_tasks",
                new_callable=AsyncMock,
                side_effect=[[task], [task]],
            ),
            patch(
                "app.api.tasks.run_in_threadpool",
                new_callable=AsyncMock,
                return_value=[{"action": "delete", "task_id": str(task.id)}],
            ),
        ):
            response = await generate_team_tasks(
                team_id=team_id,
                request=TaskGenerateRequest(minutes_id=minutes_id),
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(response.deleted_count, 0)
        self.assertFalse(task.is_deleted)

    async def test_does_not_update_completed_task(self) -> None:
        team_id = uuid4()
        minutes_id = uuid4()
        task = make_task(team_id=team_id, source_minutes_id=uuid4())
        task.status = "done"
        original_title = task.title
        session = MagicMock()
        session.commit = AsyncMock()

        with (
            patch("app.api.tasks.require_team_member", new_callable=AsyncMock),
            patch(
                "app.api.tasks._get_accessible_minutes",
                new_callable=AsyncMock,
                return_value=(
                    SimpleNamespace(id=minutes_id, body="議事録"),
                    SimpleNamespace(aiboard_payload={}),
                ),
            ),
            patch(
                "app.api.tasks._get_active_team_members",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.api.tasks._get_team_tasks",
                new_callable=AsyncMock,
                side_effect=[[task], [task]],
            ),
            patch(
                "app.api.tasks.run_in_threadpool",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "action": "update",
                        "task_id": str(task.id),
                        "title": "AIが変更しようとしたタイトル",
                        "status": "todo",
                    }
                ],
            ),
        ):
            response = await generate_team_tasks(
                team_id=team_id,
                request=TaskGenerateRequest(minutes_id=minutes_id),
                auth_user=SimpleNamespace(),
                session=session,
            )

        impacts = [
            call.args[0]
            for call in session.add.call_args_list
            if isinstance(call.args[0], TaskMinutesImpact)
        ]
        self.assertEqual(response.updated_count, 0)
        self.assertEqual(task.status, "done")
        self.assertEqual(task.title, original_title)
        self.assertEqual(impacts, [])

    async def test_records_minutes_impact_when_existing_task_is_updated(self) -> None:
        team_id = uuid4()
        minutes_id = uuid4()
        task = make_task(team_id=team_id, source_minutes_id=uuid4())
        session = MagicMock()
        session.commit = AsyncMock()

        with (
            patch("app.api.tasks.require_team_member", new_callable=AsyncMock),
            patch(
                "app.api.tasks._get_accessible_minutes",
                new_callable=AsyncMock,
                return_value=(
                    SimpleNamespace(id=minutes_id, body="議事録"),
                    SimpleNamespace(aiboard_payload={}),
                ),
            ),
            patch(
                "app.api.tasks._get_active_team_members",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.api.tasks._get_team_tasks",
                new_callable=AsyncMock,
                side_effect=[[task], [task]],
            ),
            patch(
                "app.api.tasks.run_in_threadpool",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "action": "update",
                        "task_id": str(task.id),
                        "title": "更新された確認",
                    }
                ],
            ),
        ):
            response = await generate_team_tasks(
                team_id=team_id,
                request=TaskGenerateRequest(minutes_id=minutes_id),
                auth_user=SimpleNamespace(),
                session=session,
            )

        impacts = [
            call.args[0]
            for call in session.add.call_args_list
            if isinstance(call.args[0], TaskMinutesImpact)
        ]
        self.assertEqual(response.updated_count, 1)
        self.assertEqual(len(impacts), 1)
        self.assertEqual(impacts[0].task_id, task.id)
        self.assertEqual(impacts[0].minutes_id, minutes_id)
        self.assertEqual(impacts[0].action, "updated")
        self.assertNotEqual(task.source_minutes_id, minutes_id)

    async def test_records_minutes_impact_when_task_is_created(self) -> None:
        team_id = uuid4()
        minutes_id = uuid4()
        session = MagicMock()
        session.commit = AsyncMock()

        with (
            patch("app.api.tasks.require_team_member", new_callable=AsyncMock),
            patch(
                "app.api.tasks._get_accessible_minutes",
                new_callable=AsyncMock,
                return_value=(
                    SimpleNamespace(id=minutes_id, body="議事録"),
                    SimpleNamespace(aiboard_payload={}),
                ),
            ),
            patch(
                "app.api.tasks._get_active_team_members",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.api.tasks._get_team_tasks",
                new_callable=AsyncMock,
                side_effect=[[], []],
            ),
            patch(
                "app.api.tasks.run_in_threadpool",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "action": "create",
                        "title": "新しい確認",
                        "body": "確認内容",
                        "status": "todo",
                    }
                ],
            ),
        ):
            response = await generate_team_tasks(
                team_id=team_id,
                request=TaskGenerateRequest(minutes_id=minutes_id),
                auth_user=SimpleNamespace(),
                session=session,
            )

        created_tasks = [
            call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], Task)
        ]
        impacts = [
            call.args[0]
            for call in session.add.call_args_list
            if isinstance(call.args[0], TaskMinutesImpact)
        ]
        self.assertEqual(response.created_count, 1)
        self.assertEqual(len(created_tasks), 1)
        self.assertEqual(len(impacts), 1)
        self.assertIs(impacts[0].task, created_tasks[0])
        self.assertEqual(impacts[0].minutes_id, minutes_id)
        self.assertEqual(impacts[0].action, "created")


class ListMinutesTasksTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_tasks_related_to_minutes(self) -> None:
        team_id = uuid4()
        minutes_id = uuid4()
        task = make_task(team_id=team_id, source_minutes_id=minutes_id)
        result = MagicMock()
        result.scalars.return_value.all.return_value = [task]
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        with (
            patch("app.api.tasks.require_team_member", new_callable=AsyncMock),
            patch("app.api.tasks._get_accessible_minutes", new_callable=AsyncMock),
        ):
            response = await list_minutes_tasks(
                team_id=team_id,
                minutes_id=minutes_id,
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(len(response.tasks), 1)
        self.assertEqual(response.tasks[0].id, task.id)
        statement = str(session.execute.await_args.args[0])
        self.assertIn("task_minutes_impacts", statement)


if __name__ == "__main__":
    unittest.main()
