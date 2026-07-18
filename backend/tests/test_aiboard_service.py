import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from app.services.aiboard_service import (
    AiboardRequestError,
    create_aiboard_meeting,
)


class CreateAiboardMeetingTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.aiboard_service.httpx.AsyncClient")
    async def test_reports_fastapi_validation_error_details(self, client_class) -> None:
        client = AsyncMock()
        client.post.return_value = httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "type": "missing",
                        "loc": ["body", "host_id"],
                        "msg": "Field required",
                        "input": {
                            "title": "Meeting",
                            "host_email": "host@example.com",
                        },
                    }
                ]
            },
        )
        client_class.return_value.__aenter__.return_value = client

        with self.assertLogs("app.services.aiboard_service", level="WARNING") as logs:
            with self.assertRaisesRegex(
                AiboardRequestError,
                r"aiboard request validation failed: "
                r"body\.host_id: Field required \(missing\)",
            ):
                await create_aiboard_meeting(
                    api_base_url="https://aiboard.example.com",
                    api_key="api-key",
                    title="Meeting",
                    theme="Theme",
                    host_email="host@example.com",
                    team_id=uuid4(),
                )

        self.assertIn(
            "status_code=422 detail=body.host_id: Field required (missing)",
            logs.output[0],
        )
        self.assertNotIn("host@example.com", logs.output[0])
        self.assertNotIn("api-key", logs.output[0])

    @patch("app.services.aiboard_service.httpx.AsyncClient")
    async def test_uses_generic_message_for_non_json_validation_error(
        self,
        client_class,
    ) -> None:
        client = AsyncMock()
        client.post.return_value = httpx.Response(422, text="invalid request")
        client_class.return_value.__aenter__.return_value = client

        with self.assertLogs("app.services.aiboard_service", level="WARNING") as logs:
            with self.assertRaisesRegex(
                AiboardRequestError,
                r"^aiboard request validation failed$",
            ):
                await create_aiboard_meeting(
                    api_base_url="https://aiboard.example.com",
                    api_key="api-key",
                    title="Meeting",
                    theme=None,
                    host_email="host@example.com",
                    team_id=uuid4(),
                )

        self.assertIn("status_code=422 detail=unavailable", logs.output[0])


if __name__ == "__main__":
    unittest.main()
