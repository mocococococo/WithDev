import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.api.tasks import (
    RoadmapGenerationClaim,
    RoadmapGenerationInput,
    RoadmapGenerateRequest,
    RoadmapSaveRequest,
    RoadmapStepSaveRequest,
    RoadmapStepUpdateRequest,
    TaskUpdateRequest,
    _claim_task_roadmap_generation,
    _complete_all_roadmap_steps,
    _merge_generated_roadmap,
    _reject_changes_while_roadmap_locked,
    _roadmap_generation_is_stale,
    _sync_task_completion_from_steps,
    _task_body,
    _task_roadmap_generation_error_message,
    generate_task_roadmap_endpoint,
    run_task_roadmap_generation,
    save_task_roadmap,
    update_task,
    update_roadmap_step,
)
from fastapi import HTTPException
from app.models.task import Task, TaskRoadmap, TaskRoadmapStep
from app.services.task_roadmap_service import (
    GEMINI_REQUEST_TIMEOUT_SECONDS,
    MAX_RELATED_MINUTES_CHARS,
    generate_task_roadmap,
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


def make_api_session(task: Task) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


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
        session = make_api_session(task)

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
        session = make_api_session(task)

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


class RoadmapSaveApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_saves_add_edit_delete_reorder_and_status_in_one_version(self) -> None:
        task = make_task(status="in_progress")
        removed = make_step(task.roadmap, title="削除する作業", position=0)
        retained = make_step(task.roadmap, title="残す作業", position=1)
        session = make_api_session(task)

        request = RoadmapSaveRequest(
            expected_version=task.roadmap.version,
            steps=[
                RoadmapStepSaveRequest(
                    id=retained.id,
                    title="更新した作業",
                    description="更新した説明",
                    status="in_progress",
                ),
                RoadmapStepSaveRequest(
                    title="追加した作業",
                    description="追加した説明",
                    status="todo",
                ),
            ],
        )

        with patch(
            "app.api.tasks._get_accessible_task",
            new_callable=AsyncMock,
            return_value=task,
        ):
            response = await save_task_roadmap(
                task_id=task.id,
                request=request,
                auth_user=SimpleNamespace(),
                session=session,
            )

        active = sorted(
            (step for step in task.roadmap.steps if not step.is_deleted),
            key=lambda step: step.position,
        )
        self.assertTrue(removed.is_deleted)
        self.assertTrue(removed.user_edited)
        self.assertEqual(
            [(step.title, step.status, step.position) for step in active],
            [
                ("更新した作業", "in_progress", 0),
                ("追加した作業", "todo", 1),
            ],
        )
        self.assertTrue(retained.user_edited)
        self.assertEqual(active[1].source, "user")
        self.assertTrue(active[1].user_edited)
        self.assertEqual(task.roadmap.version, 2)
        self.assertEqual(response.task.roadmap.version, 2)
        session.flush.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_all_done_steps_complete_task(self) -> None:
        task = make_task(status="in_progress")
        first = make_step(task.roadmap, title="作業1", position=0)
        second = make_step(task.roadmap, title="作業2", position=1)
        session = make_api_session(task)

        with patch(
            "app.api.tasks._get_accessible_task",
            new_callable=AsyncMock,
            return_value=task,
        ):
            await save_task_roadmap(
                task_id=task.id,
                request=RoadmapSaveRequest(
                    expected_version=task.roadmap.version,
                    steps=[
                        RoadmapStepSaveRequest(
                            id=step.id,
                            title=step.title,
                            description=step.description,
                            status="done",
                        )
                        for step in (first, second)
                    ],
                ),
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(task.status, "done")

    async def test_non_done_step_reopens_completed_task(self) -> None:
        task = make_task(status="done")
        step = make_step(task.roadmap, title="再確認", position=0, status="done")
        session = make_api_session(task)

        with patch(
            "app.api.tasks._get_accessible_task",
            new_callable=AsyncMock,
            return_value=task,
        ):
            await save_task_roadmap(
                task_id=task.id,
                request=RoadmapSaveRequest(
                    expected_version=task.roadmap.version,
                    steps=[
                        RoadmapStepSaveRequest(
                            id=step.id,
                            title=step.title,
                            description=step.description,
                            status="in_progress",
                        )
                    ],
                ),
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(task.status, "in_progress")

    async def test_rejects_stale_version_before_mutating_steps(self) -> None:
        task = make_task(status="in_progress")
        step = make_step(task.roadmap, title="変更しない", position=0)
        session = make_api_session(task)

        with (
            patch(
                "app.api.tasks._get_accessible_task",
                new_callable=AsyncMock,
                return_value=task,
            ),
            self.assertRaises(HTTPException) as raised,
        ):
            await save_task_roadmap(
                task_id=task.id,
                request=RoadmapSaveRequest(
                    expected_version=task.roadmap.version + 1,
                    steps=[
                        RoadmapStepSaveRequest(
                            id=step.id,
                            title="変更後",
                            description=step.description,
                            status=step.status,
                        )
                    ],
                ),
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(step.title, "変更しない")
        session.flush.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_task_update_does_not_mutate_roadmap(self) -> None:
        task = make_task(status="in_progress")
        step = make_step(task.roadmap, title="更新前", position=0)
        next_due_at = datetime.now(timezone.utc) + timedelta(days=1)
        session = make_api_session(task)

        with (
            patch(
                "app.api.tasks._get_accessible_task",
                new_callable=AsyncMock,
                return_value=task,
            ),
            patch(
                "app.api.tasks._get_active_team_members",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            await update_task(
                task_id=task.id,
                request=TaskUpdateRequest(due_at=next_due_at),
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(task.due_at, next_due_at)
        self.assertEqual(step.title, "更新前")
        self.assertEqual(step.status, "todo")
        self.assertEqual(task.roadmap.version, 1)
        session.commit.assert_awaited_once()


class RoadmapGenerationApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_releases_request_transaction_before_generation(self) -> None:
        task = make_task(status="in_progress")
        session = MagicMock()
        session.rollback = AsyncMock()
        generation = AsyncMock(return_value=_task_body(task))

        with (
            patch(
                "app.api.tasks._get_accessible_task",
                new_callable=AsyncMock,
                return_value=task,
            ),
            patch(
                "app.api.tasks.run_task_roadmap_generation",
                generation,
            ),
        ):
            response = await generate_task_roadmap_endpoint(
                task_id=task.id,
                request=RoadmapGenerateRequest(expected_version=task.roadmap.version),
                auth_user=SimpleNamespace(),
                session=session,
            )

        session.rollback.assert_awaited_once()
        generation.assert_awaited_once()
        self.assertEqual(response.task.id, task.id)

    async def test_returns_generation_result(self) -> None:
        refreshed_task = make_task(status="in_progress")
        refreshed_task.roadmap.overview = "生成された概要"
        refreshed_task.roadmap.generation_status = "ready"
        for index in range(3):
            make_step(
                refreshed_task.roadmap,
                title=f"生成ステップ{index}",
                position=index,
            )
        session = MagicMock()
        session.rollback = AsyncMock()
        generation = AsyncMock(return_value=_task_body(refreshed_task))

        with (
            patch(
                "app.api.tasks._get_accessible_task",
                new_callable=AsyncMock,
                return_value=refreshed_task,
            ),
            patch(
                "app.api.tasks.run_task_roadmap_generation",
                generation,
            ),
        ):
            response = await generate_task_roadmap_endpoint(
                task_id=refreshed_task.id,
                request=RoadmapGenerateRequest(
                    expected_version=refreshed_task.roadmap.version
                ),
                auth_user=SimpleNamespace(),
                session=session,
            )

        generation.assert_awaited_once()
        self.assertEqual(response.task.roadmap.generation_status, "ready")
        self.assertEqual(len(response.task.roadmap.steps), 3)


class RoadmapGenerationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_commits_generating_state_before_ai_request(self) -> None:
        task = make_task(status="in_progress")
        task.roadmap.overview = ""
        task.roadmap.input_hash = None
        task.roadmap.prompt_version = None
        task.roadmap.generation_status = "pending"
        session = MagicMock()
        session.commit = AsyncMock()

        class SessionContext:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        with (
            patch("app.api.tasks.AsyncSessionLocal", return_value=SessionContext()),
            patch(
                "app.api.tasks._get_task_for_roadmap_generation",
                new_callable=AsyncMock,
                return_value=task,
            ),
            patch(
                "app.api.tasks._get_task_related_minutes",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            claim = await _claim_task_roadmap_generation(
                task_id=task.id,
                request=RoadmapGenerateRequest(
                    expected_version=task.roadmap.version,
                ),
            )

        self.assertIsNotNone(claim.generation_input)
        self.assertIsNone(claim.task)
        session.commit.assert_awaited_once()
        self.assertEqual(task.roadmap.generation_status, "generating")
        self.assertIsNotNone(task.roadmap.generation_started_at)

    async def test_marks_generation_failed_when_saving_result_fails(self) -> None:
        task_id = uuid4()
        token = uuid4()
        generation_input = RoadmapGenerationInput(
            generation_token=token,
            task={"title": "保存する"},
            related_minutes=[],
            input_hash="input-hash",
            prompt_version="prompt-version",
        )
        failed_task = make_task()
        failed_task.id = task_id
        failed_task.roadmap.generation_status = "failed"
        mark_failed = AsyncMock(return_value=_task_body(failed_task))

        with (
            patch(
                "app.api.tasks._claim_task_roadmap_generation",
                new_callable=AsyncMock,
                return_value=RoadmapGenerationClaim(
                    generation_input=generation_input,
                    task=None,
                ),
            ),
            patch(
                "app.api.tasks.run_in_threadpool",
                new_callable=AsyncMock,
                return_value={
                    "overview": "保存する。",
                    "steps": [
                        {
                            "existing_step_id": None,
                            "title": "保存する",
                            "description": "保存を確認する。",
                        }
                    ],
                },
            ),
            patch(
                "app.api.tasks._finish_task_roadmap_generation_ready",
                new_callable=AsyncMock,
                side_effect=RuntimeError("database unavailable"),
            ),
            patch(
                "app.api.tasks._finish_task_roadmap_generation_failed",
                mark_failed,
            ),
        ):
            result = await run_task_roadmap_generation(
                task_id=task_id,
                request=RoadmapGenerateRequest(),
            )

        mark_failed.assert_awaited_once()
        self.assertEqual(result.roadmap.generation_status, "failed")

    async def test_quota_error_finishes_failed_without_retrying_ai(self) -> None:
        task_id = uuid4()
        token = uuid4()
        generation_input = RoadmapGenerationInput(
            generation_token=token,
            task={"task_id": str(task_id), "title": "生成する"},
            related_minutes=[],
            input_hash="input-hash",
            prompt_version="prompt-version",
        )
        failed_task = make_task()
        failed_task.id = task_id
        failed_task.roadmap.generation_status = "failed"
        ai_call = AsyncMock(
            side_effect=TaskRoadmapGenerationError(
                "Gemini request failed",
                reason="quota_exceeded",
            )
        )
        finish_failed = AsyncMock(return_value=_task_body(failed_task))

        with (
            patch(
                "app.api.tasks._claim_task_roadmap_generation",
                new_callable=AsyncMock,
                return_value=RoadmapGenerationClaim(
                    generation_input=generation_input,
                    task=None,
                ),
            ),
            patch("app.api.tasks.run_in_threadpool", ai_call),
            patch(
                "app.api.tasks._finish_task_roadmap_generation_failed",
                finish_failed,
            ),
        ):
            result = await run_task_roadmap_generation(
                task_id=task_id,
                request=RoadmapGenerateRequest(),
            )

        ai_call.assert_awaited_once()
        finish_failed.assert_awaited_once()
        recorded_error = finish_failed.await_args.kwargs["error"]
        self.assertEqual(recorded_error.reason, "quota_exceeded")
        self.assertEqual(result.roadmap.generation_status, "failed")


class RoadmapGenerationEditPolicyTests(unittest.TestCase):
    def test_rejects_changes_while_generation_is_queued_or_running(self) -> None:
        for generation_status in ("pending", "generating"):
            with self.subTest(generation_status=generation_status):
                roadmap = make_task().roadmap
                roadmap.generation_status = generation_status

                with self.assertRaises(HTTPException) as raised:
                    _reject_changes_while_roadmap_locked(roadmap)

                self.assertEqual(raised.exception.status_code, 409)


class RoadmapGenerationErrorMessageTests(unittest.TestCase):
    def test_explains_quota_failure_and_requires_manual_retry(self) -> None:
        message = _task_roadmap_generation_error_message(
            TaskRoadmapGenerationError(
                "Gemini request failed",
                reason="quota_exceeded",
            )
        )

        self.assertIn("利用上限", message)
        self.assertIn("手動で再試行", message)


class RoadmapGenerationLeaseTests(unittest.TestCase):
    def test_only_treats_old_or_untracked_generation_as_stale(self) -> None:
        roadmap = make_task().roadmap
        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        roadmap.generation_status = "generating"

        roadmap.generation_started_at = now - timedelta(minutes=1)
        self.assertFalse(_roadmap_generation_is_stale(roadmap, now=now))

        roadmap.generation_started_at = now - timedelta(minutes=3)
        self.assertTrue(_roadmap_generation_is_stale(roadmap, now=now))

        roadmap.generation_started_at = None
        self.assertTrue(_roadmap_generation_is_stale(roadmap, now=now))


class RoadmapResponseParsingTests(unittest.TestCase):
    def test_accepts_valid_one_to_eight_step_response(self) -> None:
        payload = {
            "overview": "リリースまでの確認を進める。",
            "steps": [
                {
                    "existing_step_id": None,
                    "title": "確認",
                    "description": "結果を記録する。",
                }
            ],
        }

        parsed = parse_task_roadmap(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(len(parsed["steps"]), 1)
        self.assertIsNone(parsed["steps"][0]["existing_step_id"])

    def test_rejects_response_without_steps(self) -> None:
        payload = {
            "overview": "不足",
            "steps": [],
        }

        with self.assertRaises(TaskRoadmapGenerationError):
            parse_task_roadmap(json.dumps(payload, ensure_ascii=False))


class RoadmapOneShotGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {
            "task_id": str(uuid4()),
            "title": "契約書を担当者へ送付する",
            "body": "確定した契約書を担当者へメールで送付する",
            "status": "todo",
            "assignee_name": "担当者",
            "due_at": None,
        }

    def test_uses_structured_output_and_calls_gemini_once(self) -> None:
        model = MagicMock()
        model.generate_content.return_value = SimpleNamespace(
            parts=[object()],
            text=json.dumps(
                {
                    "overview": "契約書を送付する。",
                    "steps": [
                        {
                            "title": "契約書を送付する",
                            "description": "担当者へのメール送信が完了している。",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

        with (
            patch(
                "app.services.task_roadmap_service.get_settings",
                return_value=SimpleNamespace(
                    gemini_api_key="test-key",
                    gemini_model="gemini-3.5-flash",
                ),
            ),
            patch("app.services.task_roadmap_service.genai.configure"),
            patch(
                "app.services.task_roadmap_service.genai.GenerativeModel",
                return_value=model,
            ),
        ):
            generated = generate_task_roadmap(
                task=self.task,
                related_minutes=[],
            )

        model.generate_content.assert_called_once()
        _, kwargs = model.generate_content.call_args
        generation_config = kwargs["generation_config"]
        request_options = kwargs["request_options"]
        steps_schema = generation_config.response_schema["properties"]["steps"]
        self.assertIsNone(generation_config.candidate_count)
        self.assertIsNone(generation_config.temperature)
        self.assertEqual(generation_config.response_mime_type, "application/json")
        self.assertEqual(steps_schema["min_items"], 1)
        self.assertEqual(steps_schema["max_items"], 8)
        self.assertIsNone(request_options.retry)
        self.assertEqual(request_options.timeout, GEMINI_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(len(generated["steps"]), 1)

    def test_raises_quota_error_without_second_ai_call(self) -> None:
        model = MagicMock()
        model.generate_content.side_effect = RuntimeError(
            "429 You exceeded your current quota"
        )

        with (
            patch(
                "app.services.task_roadmap_service.get_settings",
                return_value=SimpleNamespace(
                    gemini_api_key="test-key",
                    gemini_model="gemini-3.5-flash",
                ),
            ),
            patch("app.services.task_roadmap_service.genai.configure"),
            patch(
                "app.services.task_roadmap_service.genai.GenerativeModel",
                return_value=model,
            ),
            self.assertRaises(TaskRoadmapGenerationError) as raised,
        ):
            generate_task_roadmap(
                task=self.task,
                related_minutes=[],
            )

        model.generate_content.assert_called_once()
        self.assertEqual(raised.exception.reason, "quota_exceeded")

    def test_raises_for_invalid_structured_response_without_second_call(
        self,
    ) -> None:
        model = MagicMock()
        model.generate_content.return_value = SimpleNamespace(
            parts=[object()],
            text='{"overview": "ステップ不足", "steps": []}',
        )

        with (
            patch(
                "app.services.task_roadmap_service.get_settings",
                return_value=SimpleNamespace(
                    gemini_api_key="test-key",
                    gemini_model="gemini-3.5-flash",
                ),
            ),
            patch("app.services.task_roadmap_service.genai.configure"),
            patch(
                "app.services.task_roadmap_service.genai.GenerativeModel",
                return_value=model,
            ),
            self.assertRaises(TaskRoadmapGenerationError) as raised,
        ):
            generate_task_roadmap(
                task=self.task,
                related_minutes=[],
            )

        model.generate_content.assert_called_once()
        self.assertEqual(raised.exception.reason, "invalid_response")

    def test_raises_for_empty_response_without_second_call(self) -> None:
        model = MagicMock()
        model.generate_content.return_value = SimpleNamespace(parts=[], text="")

        with (
            patch(
                "app.services.task_roadmap_service.get_settings",
                return_value=SimpleNamespace(
                    gemini_api_key="test-key",
                    gemini_model="gemini-3.5-flash",
                ),
            ),
            patch("app.services.task_roadmap_service.genai.configure"),
            patch(
                "app.services.task_roadmap_service.genai.GenerativeModel",
                return_value=model,
            ),
            self.assertRaises(TaskRoadmapGenerationError) as raised,
        ):
            generate_task_roadmap(task=self.task, related_minutes=[])

        model.generate_content.assert_called_once()
        self.assertEqual(raised.exception.reason, "empty_response")

    def test_limits_minutes_body_in_the_single_prompt(self) -> None:
        model = MagicMock()
        model.generate_content.return_value = SimpleNamespace(
            parts=[object()],
            text=json.dumps(
                {
                    "overview": "議事録に沿って進める。",
                    "steps": [
                        {
                            "title": "対応する",
                            "description": "対応結果を確認する。",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
        minutes_body = "長" * (MAX_RELATED_MINUTES_CHARS + 100)

        with (
            patch(
                "app.services.task_roadmap_service.get_settings",
                return_value=SimpleNamespace(
                    gemini_api_key="test-key",
                    gemini_model="gemini-3.5-flash",
                ),
            ),
            patch("app.services.task_roadmap_service.genai.configure"),
            patch(
                "app.services.task_roadmap_service.genai.GenerativeModel",
                return_value=model,
            ),
        ):
            generate_task_roadmap(
                task=self.task,
                related_minutes=[
                    {
                        "minutes_id": str(uuid4()),
                        "title": "議事録",
                        "body": minutes_body,
                        "updated_at": "2026-07-30T00:00:00+00:00",
                    }
                ],
            )

        prompt = model.generate_content.call_args.args[0]
        self.assertIn("長" * MAX_RELATED_MINUTES_CHARS, prompt)
        self.assertNotIn("長" * (MAX_RELATED_MINUTES_CHARS + 1), prompt)


if __name__ == "__main__":
    unittest.main()
