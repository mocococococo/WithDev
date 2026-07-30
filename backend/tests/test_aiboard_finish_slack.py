import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from app.api.aiboard import _post_minutes_to_default_channel, settings
from app.services.slack_service import SlackApiError, SlackFileUploadResult


TEAM_ID = UUID("c50461c7-06d4-4c5e-b20e-38e8292cbe07")
MINUTES_ID = UUID("f685e91e-d8c6-47ad-8a73-42543b8baab1")
CONNECTION_ID = UUID("b3be48c0-e76b-49a5-b1de-3de2bd0941fc")
POST_LOG_ID = UUID("98079a57-f735-4c65-8e35-8d13704334ca")


def _session() -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()

    async def refresh(instance) -> None:
        if getattr(instance, "id", None) is None:
            instance.id = POST_LOG_ID
        if getattr(instance, "created_at", None) is None:
            instance.created_at = datetime(2026, 7, 31, tzinfo=timezone.utc)

    session.refresh = AsyncMock(side_effect=refresh)
    return session


def _connection() -> SimpleNamespace:
    return SimpleNamespace(
        id=CONNECTION_ID,
        bot_access_token="xoxb-token",
        default_channel_id="C123",
        default_channel_name="general",
    )


def _minutes() -> SimpleNamespace:
    return SimpleNamespace(
        id=MINUTES_ID,
        title="週次/開発会議",
        body="## 決定事項\n\n- リリースする",
    )


class FinishMeetingSlackUploadTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.api.aiboard.upload_markdown_file", new_callable=AsyncMock)
    @patch("app.api.aiboard.get_active_slack_connection", new_callable=AsyncMock)
    async def test_uploads_minutes_as_markdown_file(
        self,
        get_connection: AsyncMock,
        upload_file: AsyncMock,
    ) -> None:
        session = _session()
        get_connection.return_value = _connection()
        upload_file.return_value = SlackFileUploadResult(
            channel_id="C123",
            file_id="F123",
        )

        result = await _post_minutes_to_default_channel(
            session=session,
            team_id=TEAM_ID,
            minutes=_minutes(),
        )

        upload_file.assert_awaited_once_with(
            bot_access_token="xoxb-token",
            channel_id="C123",
            filename="週次_開発会議.md",
            title="週次/開発会議",
            content="# 週次/開発会議\n\n## 決定事項\n\n- リリースする\n",
            initial_comment=(
                "議事録を共有します。\n"
                f"タスク一覧: {settings.frontend_base_url}/teams/{TEAM_ID}/tasks"
            ),
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.channel_id, "C123")
        self.assertIsNone(result.slack_ts)

        post_log = session.add.call_args.args[0]
        self.assertEqual(post_log.status, "success")
        self.assertIsNone(post_log.slack_ts)

    @patch("app.api.aiboard.upload_markdown_file", new_callable=AsyncMock)
    @patch("app.api.aiboard.get_active_slack_connection", new_callable=AsyncMock)
    async def test_records_markdown_upload_failure(
        self,
        get_connection: AsyncMock,
        upload_file: AsyncMock,
    ) -> None:
        session = _session()
        get_connection.return_value = _connection()
        upload_file.side_effect = SlackApiError("missing_scope")

        result = await _post_minutes_to_default_channel(
            session=session,
            team_id=TEAM_ID,
            minutes=_minutes(),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_message, "missing_scope")
        post_log = session.add.call_args.args[0]
        self.assertEqual(post_log.status, "failed")
        self.assertEqual(post_log.error_message, "missing_scope")


if __name__ == "__main__":
    unittest.main()
