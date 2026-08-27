import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.api.tasks import (
    TaskChatRequest,
    chat_with_task_assistant,
    list_task_chat_messages,
)
from app.models.task import Task, TaskChatMessage, TaskRoadmap, TaskRoadmapStep
from app.services.task_chat_service import (
    TaskChatError,
    generate_task_chat_answer,
    parse_task_chat_response,
    select_relevant_minutes,
)


def make_task() -> Task:
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid4(),
        team_id=uuid4(),
        source_minutes_id=None,
        title="リリース手順を確認する",
        body="本番リリース前に手順と担当者を確認する。",
        assignee_user_id=None,
        assignee_name="Kirby",
        status="in_progress",
        due_at=None,
        is_deleted=False,
    )
    task.created_at = now
    task.updated_at = now
    task.roadmap = TaskRoadmap(
        id=uuid4(),
        overview="安全にリリースする",
        generation_status="ready",
        version=1,
        has_source_updates=False,
    )
    step = TaskRoadmapStep(
        id=uuid4(),
        roadmap_id=task.roadmap.id,
        title="チェックリストを確認する",
        description="未確認項目がない状態にする",
        status="todo",
        position=0,
        source="ai",
        user_edited=False,
        is_deleted=False,
    )
    step.created_at = now
    step.updated_at = now
    task.roadmap.steps.append(step)
    return task


class TaskChatServiceTests(unittest.TestCase):
    @staticmethod
    def _chat_input() -> dict[str, object]:
        return {
            "task": {
                "task_id": "task-1",
                "title": "リリース手順を確認する",
                "body": "本番リリース前に確認する。",
            },
            "message": "まず何をすればいい？",
            "history": [],
            "team_minutes": [],
        }

    def test_selects_related_and_relevant_team_minutes(self) -> None:
        task = {"title": "本番リリース", "body": "チェックリストを確認する"}
        team_minutes = [
            {
                "minutes_id": "recent",
                "title": "雑談",
                "body": "来月の予定",
                "updated_at": "2026-08-18T00:00:00Z",
            },
            {
                "minutes_id": "relevant",
                "title": "本番リリース会議",
                "body": "チェックリストを担当者と確認する",
                "updated_at": "2026-08-17T00:00:00Z",
            },
            {
                "minutes_id": "related",
                "title": "以前の決定",
                "body": "承認フローについて決定した",
                "updated_at": "2026-08-01T00:00:00Z",
            },
        ]

        selected = select_relevant_minutes(
            task=task,
            message="最初に何をすればいい？",
            team_minutes=team_minutes,
            related_minutes_ids={"related"},
        )

        self.assertEqual(selected[0]["minutes_id"], "related")
        self.assertEqual(selected[1]["minutes_id"], "relevant")

    def test_parses_answer_and_ignores_unknown_source_ids(self) -> None:
        updated_at = datetime.now(timezone.utc)
        available_sources = {
            "known": {
                "minutes_id": "known",
                "title": "リリース会議",
                "updated_at": updated_at,
            }
        }
        result = parse_task_chat_response(
            json.dumps(
                {
                    "answer": "まずチェックリストを確認してください。",
                    "source_minutes_ids": ["known", "unknown", "known"],
                },
                ensure_ascii=False,
            ),
            available_sources=available_sources,
        )

        self.assertEqual(result["answer"], "まずチェックリストを確認してください。")
        self.assertEqual(result["sources"], [available_sources["known"]])

    def test_rejects_response_without_answer(self) -> None:
        with self.assertRaises(TaskChatError):
            parse_task_chat_response(
                '{"source_minutes_ids": []}',
                available_sources={},
            )

    @patch("app.services.task_chat_service.genai.configure")
    @patch("app.services.task_chat_service.genai.GenerativeModel")
    @patch("app.services.task_chat_service.get_settings")
    def test_retries_invalid_response_once(
        self,
        get_settings,
        generative_model,
        _configure,
    ) -> None:
        get_settings.return_value = SimpleNamespace(
            gemini_api_key="test-key",
            gemini_model="test-model",
        )
        model = generative_model.return_value
        model.generate_content.side_effect = [
            SimpleNamespace(
                parts=[object()],
                text='{"source_minutes_ids": [], "private": "do-not-log"}',
                candidates=[SimpleNamespace(finish_reason="STOP")],
            ),
            SimpleNamespace(
                parts=[object()],
                text='{"answer": "まず担当者を確認してください。", "source_minutes_ids": []}',
                candidates=[SimpleNamespace(finish_reason="STOP")],
            ),
        ]

        with self.assertLogs("app.services.task_chat_service", level="WARNING") as logs:
            result = generate_task_chat_answer(**self._chat_input())

        self.assertEqual(result["answer"], "まず担当者を確認してください。")
        self.assertEqual(model.generate_content.call_count, 2)
        retry_prompt = model.generate_content.call_args_list[1].args[0]
        self.assertIn("再出力指示", retry_prompt)
        self.assertNotIn("do-not-log", "\n".join(logs.output))

    @patch("app.services.task_chat_service.genai.configure")
    @patch("app.services.task_chat_service.genai.GenerativeModel")
    @patch("app.services.task_chat_service.get_settings")
    def test_retries_empty_response_once(
        self,
        get_settings,
        generative_model,
        _configure,
    ) -> None:
        get_settings.return_value = SimpleNamespace(
            gemini_api_key="test-key",
            gemini_model="test-model",
        )
        model = generative_model.return_value
        model.generate_content.side_effect = [
            SimpleNamespace(parts=[], text="", candidates=[]),
            SimpleNamespace(
                parts=[object()],
                text='{"answer": "確認してください。", "source_minutes_ids": []}',
                candidates=[SimpleNamespace(finish_reason="STOP")],
            ),
        ]

        result = generate_task_chat_answer(**self._chat_input())

        self.assertEqual(result["answer"], "確認してください。")
        self.assertEqual(model.generate_content.call_count, 2)

    @patch("app.services.task_chat_service.genai.configure")
    @patch("app.services.task_chat_service.genai.GenerativeModel")
    @patch("app.services.task_chat_service.get_settings")
    def test_does_not_retry_request_failure(
        self,
        get_settings,
        generative_model,
        _configure,
    ) -> None:
        get_settings.return_value = SimpleNamespace(
            gemini_api_key="test-key",
            gemini_model="test-model",
        )
        model = generative_model.return_value
        model.generate_content.side_effect = RuntimeError("network unavailable")

        with self.assertRaises(TaskChatError) as raised:
            generate_task_chat_answer(**self._chat_input())

        self.assertEqual(raised.exception.reason, "request_failed")
        self.assertEqual(model.generate_content.call_count, 1)

    @patch("app.services.task_chat_service.genai.configure")
    @patch("app.services.task_chat_service.genai.GenerativeModel")
    @patch("app.services.task_chat_service.get_settings")
    def test_stops_after_second_invalid_response(
        self,
        get_settings,
        generative_model,
        _configure,
    ) -> None:
        get_settings.return_value = SimpleNamespace(
            gemini_api_key="test-key",
            gemini_model="test-model",
        )
        model = generative_model.return_value
        model.generate_content.return_value = SimpleNamespace(
            parts=[object()],
            text='{"source_minutes_ids": []}',
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

        with self.assertRaises(TaskChatError) as raised:
            generate_task_chat_answer(**self._chat_input())

        self.assertEqual(raised.exception.reason, "invalid_response")
        self.assertEqual(model.generate_content.call_count, 2)


class TaskChatApiTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.api.tasks.generate_task_chat_answer")
    @patch("app.api.tasks._get_task_chat_messages", new_callable=AsyncMock)
    @patch("app.api.tasks.get_current_db_user", new_callable=AsyncMock)
    @patch("app.api.tasks._get_task_related_minutes", new_callable=AsyncMock)
    @patch("app.api.tasks._get_team_minutes_for_chat", new_callable=AsyncMock)
    @patch("app.api.tasks._get_accessible_task", new_callable=AsyncMock)
    async def test_answers_with_task_and_team_minutes_context(
        self,
        get_task,
        get_team_minutes,
        get_related_minutes,
        get_current_user,
        get_chat_messages,
        generate_answer,
    ) -> None:
        task = make_task()
        now = datetime.now(timezone.utc)
        minutes_id = uuid4()
        user_id = uuid4()
        get_task.return_value = task
        get_current_user.return_value = SimpleNamespace(id=user_id)
        get_chat_messages.return_value = [
            SimpleNamespace(role="assistant", content="お手伝いします。")
        ]
        get_team_minutes.return_value = [
            {
                "minutes_id": str(minutes_id),
                "title": "リリース会議",
                "body": "承認者を確認する。",
                "updated_at": now,
            }
        ]
        get_related_minutes.return_value = [SimpleNamespace(id=minutes_id)]
        generate_answer.return_value = {
            "answer": "まず承認者を確認してください。",
            "sources": [
                {
                    "minutes_id": str(minutes_id),
                    "title": "リリース会議",
                    "updated_at": now,
                }
            ],
        }

        session = MagicMock()
        session.commit = AsyncMock()
        response = await chat_with_task_assistant(
            task_id=task.id,
            request=TaskChatRequest(message="何をすればいい？"),
            auth_user=SimpleNamespace(),
            session=session,
        )

        self.assertEqual(response.answer, "まず承認者を確認してください。")
        self.assertEqual(response.sources[0].title, "リリース会議")
        call = generate_answer.call_args.kwargs
        self.assertEqual(call["task"]["title"], task.title)
        self.assertEqual(call["task"]["roadmap"]["steps"][0]["status"], "todo")
        self.assertEqual(call["related_minutes_ids"], {str(minutes_id)})
        self.assertEqual(
            call["history"],
            [{"role": "assistant", "content": "お手伝いします。"}],
        )
        saved_messages = [call.args[0] for call in session.add.call_args_list]
        self.assertEqual([message.role for message in saved_messages], ["user", "assistant"])
        self.assertTrue(all(message.user_id == user_id for message in saved_messages))
        self.assertEqual(session.commit.await_count, 2)

    @patch("app.api.tasks._get_task_chat_messages", new_callable=AsyncMock)
    @patch("app.api.tasks.get_current_db_user", new_callable=AsyncMock)
    @patch("app.api.tasks._get_accessible_task", new_callable=AsyncMock)
    async def test_lists_only_current_users_persisted_messages(
        self,
        get_task,
        get_current_user,
        get_chat_messages,
    ) -> None:
        task = make_task()
        user_id = uuid4()
        now = datetime.now(timezone.utc)
        message = TaskChatMessage(
            id=uuid4(),
            task_id=task.id,
            user_id=user_id,
            role="assistant",
            content="保存された回答です。",
            sources=[{"title": "定例会議", "updated_at": now.isoformat()}],
        )
        message.created_at = now
        message.updated_at = now
        get_task.return_value = task
        get_current_user.return_value = SimpleNamespace(id=user_id)
        get_chat_messages.return_value = [message]

        response = await list_task_chat_messages(
            task_id=task.id,
            auth_user=SimpleNamespace(),
            session=MagicMock(),
        )

        self.assertEqual(response.messages[0].content, "保存された回答です。")
        self.assertEqual(response.messages[0].sources[0].title, "定例会議")
        self.assertEqual(get_chat_messages.call_args.kwargs["user_id"], user_id)

    @patch("app.api.tasks._get_accessible_task", new_callable=AsyncMock)
    async def test_requires_message(self, get_task) -> None:
        task = make_task()
        get_task.return_value = task

        with self.assertRaises(HTTPException) as raised:
            await chat_with_task_assistant(
                task_id=task.id,
                request=TaskChatRequest(message="   "),
                auth_user=SimpleNamespace(),
                session=MagicMock(),
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "chat message is required")


if __name__ == "__main__":
    unittest.main()
