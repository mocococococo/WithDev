import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from app.api.aiboard import (
    AiboardMeetingFinishRequest,
    AiboardSlackPostBody,
    AiboardTaskGenerationBody,
    _generate_tasks_from_minutes,
    finish_aiboard_meeting,
)
from app.services.tasks_service import TaskGenerationError


TEAM_ID = UUID("c50461c7-06d4-4c5e-b20e-38e8292cbe07")
MEETING_ID = UUID("f685e91e-d8c6-47ad-8a73-42543b8baab1")
MINUTES_ID = UUID("7cc6f990-ab53-4574-9c06-09ab2c7b0428")
NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)


def _meeting() -> SimpleNamespace:
    return SimpleNamespace(
        id=MEETING_ID,
        team_id=TEAM_ID,
        title="開発会議",
        themes=[],
        status="ended",
        started_at=NOW,
        ended_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _minutes() -> SimpleNamespace:
    return SimpleNamespace(
        id=MINUTES_ID,
        meeting_id=MEETING_ID,
        title="議事録",
        body="## 決定事項",
        created_at=NOW,
        updated_at=NOW,
    )


class FinishMeetingTaskGenerationTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.api.aiboard.generate_team_tasks_for_minutes", new_callable=AsyncMock)
    async def test_returns_task_generation_counts(
        self,
        generate_tasks: AsyncMock,
    ) -> None:
        generate_tasks.return_value = SimpleNamespace(
            created_count=2,
            updated_count=1,
            deleted_count=0,
        )
        session = MagicMock()

        result = await _generate_tasks_from_minutes(
            session=session,
            team_id=TEAM_ID,
            minutes=_minutes(),
        )

        generate_tasks.assert_awaited_once_with(
            team_id=TEAM_ID,
            minutes_id=MINUTES_ID,
            session=session,
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.created_count, 2)
        self.assertEqual(result.updated_count, 1)
        self.assertIsNone(result.error_message)

    @patch("app.api.aiboard.generate_team_tasks_for_minutes", new_callable=AsyncMock)
    async def test_reports_ai_generation_failure_without_raising(
        self,
        generate_tasks: AsyncMock,
    ) -> None:
        generate_tasks.side_effect = TaskGenerationError("Gemini request failed")

        with self.assertLogs("app.api.aiboard", level="ERROR"):
            result = await _generate_tasks_from_minutes(
                session=MagicMock(),
                team_id=TEAM_ID,
                minutes=_minutes(),
            )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.created_count, 0)
        self.assertEqual(result.error_message, "failed to generate tasks")

    async def test_finish_generates_tasks_before_posting_to_slack(self) -> None:
        call_order: list[str] = []

        async def generate_tasks(**_kwargs) -> AiboardTaskGenerationBody:
            call_order.append("tasks")
            return AiboardTaskGenerationBody(
                status="success",
                created_count=1,
            )

        async def post_to_slack(**_kwargs) -> AiboardSlackPostBody:
            call_order.append("slack")
            return AiboardSlackPostBody(status="success")

        with (
            patch(
                "app.api.aiboard._upsert_aiboard_meeting",
                new_callable=AsyncMock,
                return_value=_meeting(),
            ),
            patch(
                "app.api.aiboard._generate_and_save_minutes",
                new_callable=AsyncMock,
                return_value=_minutes(),
            ),
            patch("app.api.aiboard._generate_tasks_from_minutes", side_effect=generate_tasks),
            patch("app.api.aiboard._post_minutes_to_default_channel", side_effect=post_to_slack),
        ):
            response = await finish_aiboard_meeting(
                request=AiboardMeetingFinishRequest(
                    team_id=TEAM_ID,
                    meeting={
                        "id": str(MEETING_ID),
                        "title": "開発会議",
                    },
                ),
                _caller=SimpleNamespace(),
                session=MagicMock(),
            )

        self.assertEqual(call_order, ["tasks", "slack"])
        self.assertEqual(response.task_generation.status, "success")
        self.assertEqual(response.task_generation.created_count, 1)
        self.assertEqual(response.slack_post.status, "success")


if __name__ == "__main__":
    unittest.main()
