import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.services.team_invite_service import delete_team


class TeamDeleteTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.team_invite_service.require_team_member", new_callable=AsyncMock)
    async def test_owner_can_delete_team_with_matching_name(self, require_member) -> None:
        user = SimpleNamespace(id=uuid4())
        team = SimpleNamespace(id=uuid4(), name="デモチーム", is_deleted=False)
        membership = SimpleNamespace(role="owner")
        require_member.return_value = (user, team)
        result = MagicMock()
        result.scalar_one.return_value = membership
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()

        await delete_team(
            session=session,
            auth_user=SimpleNamespace(),
            team_id=team.id,
            confirmation_name="デモチーム",
        )

        self.assertTrue(team.is_deleted)
        session.commit.assert_awaited_once()

    @patch("app.services.team_invite_service.require_team_member", new_callable=AsyncMock)
    async def test_rejects_name_mismatch(self, require_member) -> None:
        user = SimpleNamespace(id=uuid4())
        team = SimpleNamespace(id=uuid4(), name="デモチーム", is_deleted=False)
        require_member.return_value = (user, team)
        result = MagicMock()
        result.scalar_one.return_value = SimpleNamespace(role="owner")
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()

        with self.assertRaises(HTTPException) as raised:
            await delete_team(
                session=session,
                auth_user=SimpleNamespace(),
                team_id=team.id,
                confirmation_name="別のチーム",
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "team name does not match")
        self.assertFalse(team.is_deleted)
        session.commit.assert_not_awaited()

    @patch("app.services.team_invite_service.require_team_member", new_callable=AsyncMock)
    async def test_rejects_non_owner(self, require_member) -> None:
        user = SimpleNamespace(id=uuid4())
        team = SimpleNamespace(id=uuid4(), name="デモチーム", is_deleted=False)
        require_member.return_value = (user, team)
        result = MagicMock()
        result.scalar_one.return_value = SimpleNamespace(role="admin")
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()

        with self.assertRaises(HTTPException) as raised:
            await delete_team(
                session=session,
                auth_user=SimpleNamespace(),
                team_id=team.id,
                confirmation_name="デモチーム",
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail, "team delete is forbidden")
        self.assertFalse(team.is_deleted)
        session.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
