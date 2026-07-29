import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.api.tasks import (
    TaskGenerateRequest,
    _validate_generated_task_action,
    generate_team_tasks,
    list_minutes_tasks,
)
from app.models.task import Task, TaskGenerationRun, TaskMinutesImpact


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
    async def test_returns_existing_tasks_without_regenerating_same_input(self) -> None:
        team_id = uuid4()
        minutes_id = uuid4()
        task = make_task(team_id=team_id, source_minutes_id=minutes_id)
        session = MagicMock()
        run_in_threadpool = AsyncMock()

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
                "app.api.tasks._get_successful_task_generation_run",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(),
            ),
            patch(
                "app.api.tasks._get_team_tasks",
                new_callable=AsyncMock,
                return_value=[task],
            ),
            patch("app.api.tasks.run_in_threadpool", run_in_threadpool),
        ):
            response = await generate_team_tasks(
                team_id=team_id,
                request=TaskGenerateRequest(minutes_id=minutes_id),
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(response.created_count, 0)
        self.assertEqual(response.updated_count, 0)
        self.assertEqual(len(response.tasks), 1)
        run_in_threadpool.assert_not_awaited()
        session.commit.assert_not_called()

    async def test_ignores_ai_delete_action(self) -> None:
        team_id = uuid4()
        minutes_id = uuid4()
        task = make_task(team_id=team_id, source_minutes_id=uuid4())
        session = MagicMock()
        session.commit = AsyncMock()

        with (
            patch("app.api.tasks.require_team_member", new_callable=AsyncMock),
            patch(
                "app.api.tasks._get_successful_task_generation_run",
                new_callable=AsyncMock,
                return_value=None,
            ),
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
                "app.api.tasks._get_successful_task_generation_run",
                new_callable=AsyncMock,
                return_value=None,
            ),
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
                "app.api.tasks._get_successful_task_generation_run",
                new_callable=AsyncMock,
                return_value=None,
            ),
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
        generation_runs = [
            call.args[0]
            for call in session.add.call_args_list
            if isinstance(call.args[0], TaskGenerationRun)
        ]
        self.assertEqual(response.updated_count, 1)
        self.assertEqual(len(impacts), 1)
        self.assertEqual(len(generation_runs), 1)
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
                "app.api.tasks._get_successful_task_generation_run",
                new_callable=AsyncMock,
                return_value=None,
            ),
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


class GeneratedTaskActionValidationTests(unittest.TestCase):
    def test_rejects_status_regression(self) -> None:
        task = make_task(team_id=uuid4())
        task.status = "in_progress"

        validated = _validate_generated_task_action(
            action={
                "action": "update",
                "task_id": str(task.id),
                "status": "todo",
            },
            existing_task_map={task.id: task},
            member_map={},
        )

        self.assertIsNone(validated)
        self.assertEqual(task.status, "in_progress")

    def test_allows_forward_status_transition(self) -> None:
        task = make_task(team_id=uuid4())

        validated = _validate_generated_task_action(
            action={
                "action": "update",
                "task_id": str(task.id),
                "status": "done",
            },
            existing_task_map={task.id: task},
            member_map={},
        )

        self.assertEqual(validated["status"], "done")

    def test_rejects_entire_update_when_due_at_is_invalid(self) -> None:
        task = make_task(team_id=uuid4())

        validated = _validate_generated_task_action(
            action={
                "action": "update",
                "task_id": str(task.id),
                "title": "変更されるべきではない",
                "due_at": "not-a-date",
            },
            existing_task_map={task.id: task},
            member_map={},
        )

        self.assertIsNone(validated)
        self.assertEqual(task.title, "確認する")

    def test_rejects_create_with_invalid_status(self) -> None:
        validated = _validate_generated_task_action(
            action={
                "action": "create",
                "title": "新しいタスク",
                "body": "本文",
                "status": "invalid",
            },
            existing_task_map={},
            member_map={},
        )

        self.assertIsNone(validated)

    def test_rejects_entire_update_when_assignee_is_not_team_member(self) -> None:
        task = make_task(team_id=uuid4())

        validated = _validate_generated_task_action(
            action={
                "action": "update",
                "task_id": str(task.id),
                "title": "変更されるべきではない",
                "assignee_user_id": str(uuid4()),
                "assignee_name": "チーム外",
            },
            existing_task_map={task.id: task},
            member_map={},
        )

        self.assertIsNone(validated)
        self.assertEqual(task.title, "確認する")


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
