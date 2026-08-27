import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register every model referenced by foreign keys
from app.api.tasks import (
    RoadmapGenerateRequest,
    _claim_task_roadmap_generation,
    _commit_and_reload_task,
    _finish_task_roadmap_generation_failed,
    _finish_task_roadmap_generation_ready,
    _task_body,
    run_task_roadmap_generation,
)
from app.db.base import Base
from app.models.task import Task, TaskRoadmap, TaskRoadmapStep
from app.models.team import Team
from app.services.task_roadmap_service import TaskRoadmapGenerationError


class RoadmapPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        database_url = os.getenv("ROADMAP_TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("ROADMAP_TEST_DATABASE_URL is not configured")
        database_name = make_url(database_url).database or ""
        if "test" not in database_name.lower():
            raise RuntimeError(
                "ROADMAP_TEST_DATABASE_URL must point to a disposable test database"
            )

        self.engine = create_async_engine(database_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("app.api.tasks.AsyncSessionLocal", self.sessions)
        self.session_patch.start()

    async def asyncTearDown(self) -> None:
        if not hasattr(self, "engine"):
            return
        self.session_patch.stop()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def _create_pending_task(self) -> UUID:
        team_id = uuid4()
        task_id = uuid4()
        async with self.sessions() as session:
            team = Team(id=team_id, name="roadmap integration", is_deleted=False)
            task = Task(
                id=task_id,
                team_id=team_id,
                source_minutes_id=None,
                title="リリース準備",
                body="変更を確認して安全にリリースする",
                assignee_user_id=None,
                assignee_name=None,
                status="in_progress",
                due_at=None,
                is_deleted=False,
            )
            task.roadmap = TaskRoadmap(
                overview="",
                generation_status="pending",
                version=1,
                has_source_updates=False,
            )
            session.add_all([team, task])
            await session.commit()
        return task_id

    async def _get_roadmap(self, task_id: UUID) -> TaskRoadmap:
        async with self.sessions() as session:
            result = await session.execute(
                select(TaskRoadmap).where(TaskRoadmap.task_id == task_id)
            )
            roadmap = result.scalar_one()
            await session.refresh(roadmap, attribute_names=["steps"])
            return roadmap

    async def test_concurrent_requests_call_ai_once_and_persist_one_result(self) -> None:
        task_id = await self._create_pending_task()
        generated = {
            "overview": "リリースまでの確認を進める。",
            "steps": [
                {
                    "existing_step_id": None,
                    "title": "変更を確認する",
                    "description": "確認結果が記録されている。",
                }
            ],
        }
        ai_call = AsyncMock(return_value=generated)

        with patch("app.api.tasks.run_in_threadpool", ai_call):
            await asyncio.gather(
                run_task_roadmap_generation(
                    task_id=task_id,
                    request=RoadmapGenerateRequest(expected_version=1),
                ),
                run_task_roadmap_generation(
                    task_id=task_id,
                    request=RoadmapGenerateRequest(expected_version=1),
                ),
            )

        self.assertEqual(ai_call.await_count, 1)
        roadmap = await self._get_roadmap(task_id)
        self.assertEqual(roadmap.generation_status, "ready")
        self.assertEqual(roadmap.overview, generated["overview"])
        self.assertEqual(len(roadmap.steps), 1)
        self.assertEqual(roadmap.steps[0].title, "変更を確認する")

    async def test_commit_reload_keeps_roadmap_steps_available_for_response(self) -> None:
        task_id = await self._create_pending_task()
        async with self.sessions() as session:
            result = await session.execute(
                select(TaskRoadmap).where(TaskRoadmap.task_id == task_id)
            )
            roadmap = result.scalar_one()
            roadmap.generation_status = "ready"
            roadmap.steps.append(
                TaskRoadmapStep(
                    title="保存確認",
                    description="更新後のレスポンスに含まれる。",
                    status="todo",
                    position=0,
                    source="ai",
                    user_edited=False,
                    is_deleted=False,
                )
            )
            await session.commit()

        async with self.sessions() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one()
            task.due_at = datetime.now(timezone.utc)
            reloaded = await _commit_and_reload_task(
                session=session,
                task=task,
            )
            body = _task_body(reloaded)

        self.assertEqual(body.id, task_id)
        self.assertIsNotNone(body.roadmap)
        self.assertEqual([step.title for step in body.roadmap.steps], ["保存確認"])

    async def test_ai_wait_does_not_keep_the_claim_transaction_open(self) -> None:
        task_id = await self._create_pending_task()

        claim = await _claim_task_roadmap_generation(
            task_id=task_id,
            request=RoadmapGenerateRequest(expected_version=1),
        )

        self.assertIsNotNone(claim.generation_input)
        async with self.sessions() as independent_session:
            result = await independent_session.execute(
                select(Task).where(Task.id == task_id).with_for_update()
            )
            self.assertEqual(result.scalar_one().id, task_id)
            await independent_session.rollback()

    async def test_late_result_from_stale_generation_is_discarded(self) -> None:
        task_id = await self._create_pending_task()
        first_claim = await _claim_task_roadmap_generation(
            task_id=task_id,
            request=RoadmapGenerateRequest(expected_version=1),
        )
        self.assertIsNotNone(first_claim.generation_input)

        async with self.sessions() as session:
            result = await session.execute(
                select(TaskRoadmap)
                .where(TaskRoadmap.task_id == task_id)
                .with_for_update()
            )
            roadmap = result.scalar_one()
            roadmap.generation_started_at = datetime.now(timezone.utc) - timedelta(
                minutes=3
            )
            await session.commit()

        second_claim = await _claim_task_roadmap_generation(
            task_id=task_id,
            request=RoadmapGenerateRequest(),
        )
        self.assertIsNotNone(second_claim.generation_input)
        self.assertNotEqual(
            first_claim.generation_input.generation_token,
            second_claim.generation_input.generation_token,
        )

        stale_result = {
            "overview": "古い結果",
            "steps": [
                {
                    "existing_step_id": None,
                    "title": "古いステップ",
                    "description": "保存してはいけない。",
                }
            ],
        }
        await _finish_task_roadmap_generation_ready(
            task_id=task_id,
            generation_token=first_claim.generation_input.generation_token,
            generation_input=first_claim.generation_input,
            generated=stale_result,
        )

        roadmap = await self._get_roadmap(task_id)
        self.assertEqual(roadmap.generation_status, "generating")
        self.assertEqual(roadmap.overview, "")
        self.assertEqual(roadmap.steps, [])

        current_result = {
            "overview": "新しい結果",
            "steps": [
                {
                    "existing_step_id": None,
                    "title": "新しいステップ",
                    "description": "こちらだけを保存する。",
                }
            ],
        }
        await _finish_task_roadmap_generation_ready(
            task_id=task_id,
            generation_token=second_claim.generation_input.generation_token,
            generation_input=second_claim.generation_input,
            generated=current_result,
        )

        roadmap = await self._get_roadmap(task_id)
        self.assertEqual(roadmap.generation_status, "ready")
        self.assertEqual(roadmap.overview, "新しい結果")
        self.assertEqual([step.title for step in roadmap.steps], ["新しいステップ"])

    async def test_late_result_after_task_deletion_is_discarded(self) -> None:
        task_id = await self._create_pending_task()
        claim = await _claim_task_roadmap_generation(
            task_id=task_id,
            request=RoadmapGenerateRequest(expected_version=1),
        )
        self.assertIsNotNone(claim.generation_input)

        async with self.sessions() as session:
            task_result = await session.execute(
                select(Task).where(Task.id == task_id).with_for_update()
            )
            task = task_result.scalar_one()
            roadmap_result = await session.execute(
                select(TaskRoadmap).where(TaskRoadmap.task_id == task_id)
            )
            roadmap = roadmap_result.scalar_one()
            task.is_deleted = True
            roadmap.generation_status = "failed"
            roadmap.generation_token = None
            roadmap.generation_started_at = None
            roadmap.version += 1
            await session.commit()

        await _finish_task_roadmap_generation_ready(
            task_id=task_id,
            generation_token=claim.generation_input.generation_token,
            generation_input=claim.generation_input,
            generated={
                "overview": "削除後に到着した結果",
                "steps": [
                    {
                        "existing_step_id": None,
                        "title": "保存してはいけない",
                        "description": "削除済みタスクの結果。",
                    }
                ],
            },
        )

        roadmap = await self._get_roadmap(task_id)
        self.assertEqual(roadmap.generation_status, "failed")
        self.assertEqual(roadmap.overview, "")
        self.assertEqual(roadmap.steps, [])

    async def test_quota_failure_is_persisted_without_a_fallback_step(self) -> None:
        task_id = await self._create_pending_task()
        claim = await _claim_task_roadmap_generation(
            task_id=task_id,
            request=RoadmapGenerateRequest(expected_version=1),
        )
        self.assertIsNotNone(claim.generation_input)

        await _finish_task_roadmap_generation_failed(
            task_id=task_id,
            generation_token=claim.generation_input.generation_token,
            error=TaskRoadmapGenerationError(
                "Gemini request failed",
                reason="quota_exceeded",
            ),
        )

        roadmap = await self._get_roadmap(task_id)
        self.assertEqual(roadmap.generation_status, "failed")
        self.assertIn("利用上限", roadmap.generation_error)
        self.assertEqual(roadmap.steps, [])


if __name__ == "__main__":
    unittest.main()
