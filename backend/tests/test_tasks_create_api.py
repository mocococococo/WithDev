import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.api.tasks import TaskCreateRequest, create_team_task


class CreateTeamTaskTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.api.tasks._get_active_team_members", new_callable=AsyncMock)
    @patch("app.api.tasks.require_team_member", new_callable=AsyncMock)
    async def test_creates_manual_task(
        self,
        require_member,
        get_members,
    ) -> None:
        team_id = uuid4()
        assignee_id = uuid4()
        assignee = SimpleNamespace(id=assignee_id, display_name="Kirby", email="kirby@example.com")
        get_members.return_value = [(assignee, "member")]
        session = MagicMock()
        session.commit = AsyncMock()
        now = datetime.now(timezone.utc)

        async def commit_task() -> None:
            task = session.add.call_args.args[0]
            task.id = uuid4()
            task.created_at = now
            task.updated_at = now

        session.commit.side_effect = commit_task
        result = MagicMock()
        result.scalar_one_or_none.side_effect = lambda: session.add.call_args.args[0]
        session.execute = AsyncMock(return_value=result)

        response = await create_team_task(
            team_id=team_id,
            request=TaskCreateRequest(
                title="リリース手順を確認する",
                body="手順書と実際の操作を照合する。",
                assignee_user_id=assignee_id,
                status="in_progress",
                due_at=now,
            ),
            auth_user=SimpleNamespace(),
            session=session,
        )

        require_member.assert_awaited_once()
        created_task = session.add.call_args.args[0]
        self.assertEqual(created_task.team_id, team_id)
        self.assertIsNone(created_task.source_minutes_id)
        self.assertEqual(created_task.title, "リリース手順を確認する")
        self.assertEqual(created_task.body, "手順書と実際の操作を照合する。")
        self.assertEqual(created_task.assignee_user_id, assignee_id)
        self.assertEqual(created_task.assignee_name, "Kirby")
        self.assertEqual(created_task.status, "in_progress")
        self.assertEqual(response.task.id, created_task.id)
        session.commit.assert_awaited_once()
        session.execute.assert_awaited_once()

    @patch("app.api.tasks.require_team_member", new_callable=AsyncMock)
    async def test_requires_title(self, require_member) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        with self.assertRaises(HTTPException) as raised:
            await create_team_task(
                team_id=uuid4(),
                request=TaskCreateRequest(title="   "),
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "task title is required")
        session.add.assert_not_called()

    @patch("app.api.tasks._get_active_team_members", new_callable=AsyncMock)
    @patch("app.api.tasks.require_team_member", new_callable=AsyncMock)
    async def test_rejects_non_member_assignee(
        self,
        require_member,
        get_members,
    ) -> None:
        get_members.return_value = []
        session = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        with self.assertRaises(HTTPException) as raised:
            await create_team_task(
                team_id=uuid4(),
                request=TaskCreateRequest(
                    title="担当者を確認する",
                    assignee_user_id=uuid4(),
                ),
                auth_user=SimpleNamespace(),
                session=session,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.detail,
            "assignee_user_id must be a team member",
        )
        session.add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
