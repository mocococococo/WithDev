import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.services.slack_service import (
    SLACK_BOT_SCOPES,
    SLACK_COMPLETE_UPLOAD_EXTERNAL_URL,
    SLACK_CONVERSATIONS_JOIN_URL,
    SLACK_GET_UPLOAD_URL_EXTERNAL_URL,
    SlackApiError,
    build_minutes_markdown,
    build_minutes_markdown_filename,
    upload_markdown_file,
)


class MinutesMarkdownTests(unittest.TestCase):
    def test_builds_markdown_document(self) -> None:
        self.assertEqual(
            build_minutes_markdown(title="週次会議", body="## 決定事項\n\n- リリースする"),
            "# 週次会議\n\n## 決定事項\n\n- リリースする\n",
        )

    def test_builds_safe_markdown_filename(self) -> None:
        self.assertEqual(
            build_minutes_markdown_filename(title=' 週次/会議: "開発" '),
            "週次_会議_ _開発_.md",
        )

    def test_file_write_scope_is_requested(self) -> None:
        self.assertIn("files:write", SLACK_BOT_SCOPES)
        self.assertIn("channels:join", SLACK_BOT_SCOPES)


class UploadMarkdownFileTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.slack_service.httpx.AsyncClient")
    async def test_uploads_and_shares_markdown_file(self, client_class) -> None:
        client = AsyncMock()
        client.post.side_effect = [
            httpx.Response(
                200,
                json={
                    "ok": True,
                    "channel": {"id": "C123"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "ok": True,
                    "upload_url": "https://files.slack.test/upload",
                    "file_id": "F123",
                },
            ),
            httpx.Response(200, text="OK"),
            httpx.Response(
                200,
                json={
                    "ok": True,
                    "files": [{"id": "F123"}],
                },
            ),
        ]
        client_class.return_value.__aenter__.return_value = client
        content = "# 議事録\n\n決定事項"

        result = await upload_markdown_file(
            bot_access_token="xoxb-token",
            channel_id="C123",
            filename="議事録.md",
            title="議事録",
            content=content,
        )

        self.assertEqual(result.channel_id, "C123")
        self.assertEqual(result.file_id, "F123")
        self.assertEqual(client.post.await_count, 4)

        join_channel = client.post.await_args_list[0]
        self.assertEqual(join_channel.args[0], SLACK_CONVERSATIONS_JOIN_URL)
        self.assertEqual(join_channel.kwargs["json"], {"channel": "C123"})

        request_upload_url = client.post.await_args_list[1]
        self.assertEqual(request_upload_url.args[0], SLACK_GET_UPLOAD_URL_EXTERNAL_URL)
        self.assertEqual(request_upload_url.kwargs["data"]["filename"], "議事録.md")
        self.assertEqual(
            request_upload_url.kwargs["data"]["length"],
            len(content.encode("utf-8")),
        )

        upload_file = client.post.await_args_list[2]
        self.assertEqual(upload_file.args[0], "https://files.slack.test/upload")
        self.assertEqual(upload_file.kwargs["content"], content.encode("utf-8"))

        complete_upload = client.post.await_args_list[3]
        self.assertEqual(complete_upload.args[0], SLACK_COMPLETE_UPLOAD_EXTERNAL_URL)
        self.assertEqual(complete_upload.kwargs["json"]["channel_id"], "C123")
        self.assertEqual(
            complete_upload.kwargs["json"]["files"],
            [{"id": "F123", "title": "議事録"}],
        )

    @patch("app.services.slack_service.httpx.AsyncClient")
    async def test_reports_binary_upload_failure(self, client_class) -> None:
        client = AsyncMock()
        client.post.side_effect = [
            httpx.Response(
                200,
                json={
                    "ok": True,
                    "channel": {"id": "C123"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "ok": True,
                    "upload_url": "https://files.slack.test/upload",
                    "file_id": "F123",
                },
            ),
            httpx.Response(500, text="failed"),
        ]
        client_class.return_value.__aenter__.return_value = client

        with self.assertRaisesRegex(SlackApiError, "slack file upload failed: 500"):
            await upload_markdown_file(
                bot_access_token="xoxb-token",
                channel_id="C123",
                filename="議事録.md",
                title="議事録",
                content="# 議事録",
            )


if __name__ == "__main__":
    unittest.main()
