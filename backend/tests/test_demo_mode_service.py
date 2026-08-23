import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.models.meeting import Meeting
from app.models.team import Team, TeamMember
from app.models.user import User
from app.services.aiboard_service import AiboardCreatedMeeting
from app.services.demo_mode_service import (
    DEMO_MEETING_THEME,
    DEMO_MEETING_TITLE,
    DEMO_TEAM_NAME,
    create_demo_mode_workspace,
)


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commit = AsyncMock()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()


class DemoModeServiceTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.demo_mode_service.create_aiboard_meeting", new_callable=AsyncMock)
    @patch("app.services.demo_mode_service.upsert_authenticated_user", new_callable=AsyncMock)
    async def test_creates_three_member_team_and_meeting(
        self,
        upsert_user,
        create_meeting,
    ) -> None:
        current_user_id = uuid4()
        meeting_id = uuid4()
        upsert_user.return_value = SimpleNamespace(id=current_user_id)
        create_meeting.return_value = AiboardCreatedMeeting(
            id=meeting_id,
            host_id="host-id",
            team_id="filled-after-team-is-created",
            title=DEMO_MEETING_TITLE,
            themes=[{"title": DEMO_MEETING_THEME}],
            created_at_ms=1_700_000_000_000,
            payload={"id": str(meeting_id)},
        )
        session = _FakeSession()
        auth_user = SimpleNamespace(
            uid="firebase-user",
            email="owner@example.com",
            claims={"email": "owner@example.com"},
        )

        result = await create_demo_mode_workspace(
            session=session,
            auth_user=auth_user,
            aiboard_api_base_url="https://aiboard.example.com",
            aiboard_api_key="secret",
        )

        team = next(item for item in session.added if isinstance(item, Team))
        demo_users = [item for item in session.added if isinstance(item, User)]
        memberships = [item for item in session.added if isinstance(item, TeamMember)]
        meeting = next(item for item in session.added if isinstance(item, Meeting))

        self.assertEqual(team.name, DEMO_TEAM_NAME)
        self.assertEqual(
            [user.display_name for user in demo_users],
            ["デモメンバー1", "デモメンバー2"],
        )
        self.assertEqual(len(memberships), 3)
        self.assertEqual(
            {(member.user_id, member.role) for member in memberships},
            {
                (current_user_id, "owner"),
                (demo_users[0].id, "member"),
                (demo_users[1].id, "member"),
            },
        )
        self.assertEqual(meeting.id, meeting_id)
        self.assertEqual(meeting.team_id, team.id)
        self.assertEqual(meeting.title, DEMO_MEETING_TITLE)
        self.assertEqual(meeting.themes, [{"title": DEMO_MEETING_THEME}])
        self.assertEqual(result.team.id, team.id)
        self.assertEqual(result.team.member_count, 3)
        self.assertEqual(result.meeting_id, meeting_id)
        create_meeting.assert_awaited_once_with(
            api_base_url="https://aiboard.example.com",
            api_key="secret",
            title=DEMO_MEETING_TITLE,
            theme=DEMO_MEETING_THEME,
            host_email="owner@example.com",
            team_id=team.id,
        )
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
