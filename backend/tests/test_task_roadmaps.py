import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from uuid import uuid4

from app.api.tasks import (
    RoadmapStepUpdateRequest,
    _complete_all_roadmap_steps,
    _merge_generated_roadmap,
    _sync_task_completion_from_steps,
    update_roadmap_step,
)
from app.models.task import Task, TaskRoadmap, TaskRoadmapStep
from app.services.task_roadmap_service import (
    TaskRoadmapGenerationError,
    parse_task_roadmap,
)


def make_task(*, status: str = "in_progress") -> Task:
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid4(),
        team_id=uuid4(),
        source_minutes_id=None,
        title="リリースする",
        body="新しいバージョンを安全にリリースする",
        assignee_user_id=None,
        assignee_name=None,
        status=status,
        due_at=None,
        is_deleted=False,
    )
    task.created_at = now
    task.updated_at = now
    task.roadmap = TaskRoadmap(
        id=uuid4(),
        overview="以前の概要",
        generation_status="ready",
        version=1,
        has_source_updates=False,
    )
    task.roadmap.created_at = now
    task.roadmap.updated_at = now
    return task


def make_step(
    roadmap: TaskRoadmap,
    *,
    title: str,
    position: int,
    source: str = "ai",
    user_edited: bool = False,
    status: str = "todo",
    is_deleted: bool = False,
) -> TaskRoadmapStep:
    now = datetime.now(timezone.utc)
    step = TaskRoadmapStep(
        id=uuid4(),
        roadmap_id=roadmap.id,
        title=title,
        description=f"{title}の説明",
        status=status,
        position=position,
        source=source,
        user_edited=user_edited,
        is_deleted=is_deleted,
    )
    step.created_at = now
    step.updated_at = now
    roadmap.steps.append(step)
    return step


class RoadmapMergeTests(unittest.TestCase):
    def test_preserves_user_work_and_replaces_only_untouched_ai_steps(self) -> None:
        task = make_task()
        roadmap = task.roadmap
        edited = make_step(
            roadmap,
            title="ユーザーが編集した確認",
            position=0,
            user_edited=True,
        )
        completed = make_step(
            roadmap,
            title="完了済みの確認",
            position=1,
            status="done",
        )
        obsolete = make_step(roadmap, title="古い未着手ステップ", position=2)
        deleted = make_step(
            roadmap,
            title="復活させないステップ",
            position=3,
            is_deleted=True,
            user_edited=True,
        )

        _merge_generated_roadmap(
            task=task,
            roadmap=roadmap,
            generated={
                "overview": "新しい概要",
                "steps": [
                    {
                        "existing_step_id": edited.id,
                        "title": "AIによる上書き",
                        "description": "上書きしてはいけない",
                    },
                    {
                        "existing_step_id": completed.id,
                        "title": "完了済みの更新",
                        "description": "完了済みも上書きしない",
                    },
                    {
                        "existing_step_id": None,
                        "title": deleted.title,
                        "description": "削除済みを復活させない",
                    },
                    {
                        "existing_step_id": None,
                        "title": "新しい検証",
                        "description": "検証結果を記録する",
                    },
                ],
            },
        )

        active = [step for step in roadmap.steps if not step.is_deleted]
        self.assertEqual(roadmap.overview, "新しい概要")
        self.assertEqual(edited.title, "ユーザーが編集した確認")
        self.assertEqual(completed.title, "完了済みの確認")
        self.assertTrue(obsolete.is_deleted)
        self.assertEqual(active[-1].title, "新しい検証")
        self.assertEqual(active[-1].status, "todo")
        self.assertEqual(active[-1].source, "ai")

    def test_new_steps_are_done_when_initial_task_is_done(self) -> None:
        task = make_task(status="done")
        _merge_generated_roadmap(
            task=task,
            roadmap=task.roadmap,
            generated={
                "overview": "完了済みタスク",
                "steps": [
                    {"existing_step_id": None, "title": f"作業{i}", "description": "完了"}
                    for i in range(3)
                ],
            },
        )
        self.assertTrue(all(step.status == "done" for step in task.roadmap.steps))


class RoadmapCompletionTests(unittest.TestCase):
    def test_completes_task_when_every_active_step_is_done(self) -> None:
        task = make_task(status="in_progress")
        make_step(task.roadmap, title="作業1", position=0, status="done")
        make_step(task.roadmap, title="作業2", position=1, status="done")

        _sync_task_completion_from_steps(task, task.roadmap)

        self.assertEqual(task.status, "done")

    def test_completing_task_completes_every_active_step(self) -> None:
        task = make_task(status="done")
        first = make_step(task.roadmap, title="作業1", position=0)
        second = make_step(task.roadmap, title="作業2", position=1, status="in_progress")

        _complete_all_roadmap_steps(task.roadmap)

        self.assertEqual(first.status, "done")
        self.assertEqual(second.status, "done")


class RoadmapStepApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_completing_last_step_completes_task(self) -> None:
        task = make_task(status="in_progress")
        first = make_step(task.roadmap, title="作業1", position=0, status="done")
        last = make_step(task.roadmap, title="作業2", position=1, status="in_progress")
        session = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        with patch(
            "app.api.tasks._get_accessible_task",
            new_callable=AsyncMock,
            return_value=task,
        ):
            await update_roadmap_step(
                task_id=task.id,
                step_id=last.id,
                request=RoadmapStepUpdateRequest(status="done"),
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(first.status, "done")
        self.assertEqual(last.status, "done")
        self.assertEqual(task.status, "done")

    async def test_reopening_step_reopens_completed_task(self) -> None:
        task = make_task(status="done")
        step = make_step(task.roadmap, title="追加確認", position=0, status="done")
        session = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        with patch(
            "app.api.tasks._get_accessible_task",
            new_callable=AsyncMock,
            return_value=task,
        ):
            await update_roadmap_step(
                task_id=task.id,
                step_id=step.id,
                request=RoadmapStepUpdateRequest(
                    status="in_progress",
                    reopen_task=True,
                ),
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(step.status, "in_progress")
        self.assertEqual(task.status, "in_progress")


class RoadmapResponseParsingTests(unittest.TestCase):
    def test_accepts_valid_three_to_eight_step_response(self) -> None:
        payload = {
            "overview": "リリースまでの確認を進める。",
            "steps": [
                {
                    "existing_step_id": None,
                    "title": f"確認{i}",
                    "description": "結果を記録する。",
                }
                for i in range(3)
            ],
        }

        parsed = parse_task_roadmap(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(len(parsed["steps"]), 3)

    def test_rejects_response_with_too_few_steps(self) -> None:
        payload = {
            "overview": "不足",
            "steps": [
                {
                    "existing_step_id": None,
                    "title": "確認",
                    "description": "結果を記録する。",
                }
            ],
        }

        with self.assertRaises(TaskRoadmapGenerationError):
            parse_task_roadmap(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
