import hashlib
import asyncio
import hmac
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

import app.models  # noqa: F401
from app.api.task_assignments import (
    get_task_assignment, receive_slack_callback, start_task_assignment, start_team_assignments,
)
from app.db.base import Base
from app.models.slack import SlackConnection
from app.models.meeting import Meeting
from app.models.minutes import MeetingMinutes
from app.models.task import Task
from app.models.task_assignment import TaskAssignment, TaskAssignmentAttempt, TaskAssignmentEvent
from app.models.team import Team, TeamMember
from app.models.user import User
from app.services.assignment_agent_service import AssignmentAgentError, rank_assignment_candidates
from app.services.slack_assignment_transport import ACTION_PREFIX, parse_due_date, verify_slack_signature
from app.services.task_assignment_service import (
    dispatch_next_assignment, expire_assignments, handle_assignment_event,
    process_next_event, select_next_assignment, task_snapshot,
)

SERVICE = "app.services.task_assignment_service."
SECRET = "test-signing-secret"


def signed_request(payload, *, form=False, timestamp=None):
    body = (urlencode({"payload": json.dumps(payload)}).encode() if form else json.dumps(payload).encode())
    timestamp = str(timestamp or int(time.time()))
    signature = "v0=" + hmac.new(SECRET.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()

    async def receive():
        return {"type": "http.request", "body": body}

    return Request({
        "type": "http", "method": "POST", "path": "/api/slack/events",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded" if form else b"application/json"),
                    (b"x-slack-request-timestamp", timestamp.encode()), (b"x-slack-signature", signature.encode())],
    }, receive)


class AssignmentSecurityTests(unittest.TestCase):
    def test_signature_rejects_old_and_modified_requests(self):
        timestamp = str(int(time.time()))
        body = b'{"text":"hello"}'
        signature = "v0=" + hmac.new(SECRET.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_slack_signature(body=body, timestamp=timestamp, signature=signature, secret=SECRET))
        self.assertFalse(verify_slack_signature(body=body + b" ", timestamp=timestamp, signature=signature, secret=SECRET))
        self.assertFalse(verify_slack_signature(body=body, timestamp=str(int(timestamp) - 301), signature=signature, secret=SECRET))

    @patch("app.services.assignment_agent_service._generate")
    def test_agent_cannot_nominate_non_members_or_omit_candidates(self, generate):
        members = [{"user_id": str(uuid4())}, {"user_id": str(uuid4())}]
        for ids in [[str(uuid4())], [members[0]["user_id"]], [members[0]["user_id"]] * 2]:
            generate.return_value = {"candidates": [{"user_id": value, "reason": "理由"} for value in ids]}
            with self.assertRaises(AssignmentAgentError):
                rank_assignment_candidates(task={}, members=members)

    def test_date_is_end_of_day_in_tokyo(self):
        due = parse_due_date("2026-10-30")
        self.assertEqual(due.isoformat(), "2026-10-30T23:59:59+09:00")


class AssignmentPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database_url = os.getenv("ASSIGNMENT_TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("ASSIGNMENT_TEST_DATABASE_URL is not configured")
        if "test" not in (make_url(database_url).database or "").lower():
            raise RuntimeError("ASSIGNMENT_TEST_DATABASE_URL must point to a disposable test database")
        self.engine = create_async_engine(database_url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch(SERVICE + "AsyncSessionLocal", self.sessions)
        self.session_patch.start()
        self.post = AsyncMock(return_value=SimpleNamespace(slack_ts="100.1", channel_id="D1"))
        self.post_patch = patch(SERVICE + "post_message", self.post)
        self.post_patch.start()
        self.dm_patch = patch(SERVICE + "open_assignment_dm", AsyncMock(return_value=("U1", "D1")))
        self.dm_patch.start()
        self.team_id, self.user_id, self.other_user_id = uuid4(), uuid4(), uuid4()
        self.task_id, self.assignment_id, self.connection_id = uuid4(), uuid4(), uuid4()
        async with self.sessions() as session:
            session.add(Team(id=self.team_id, name="開発", is_deleted=False))
            session.add_all([
                User(id=self.user_id, firebase_uid="user1", email="one@example.com", display_name="一郎", is_deleted=False),
                User(id=self.other_user_id, firebase_uid="user2", email="two@example.com", display_name="二郎", is_deleted=False),
            ])
            await session.flush()
            session.add_all([
                TeamMember(team_id=self.team_id, user_id=self.user_id, role="member"),
                TeamMember(team_id=self.team_id, user_id=self.other_user_id, role="member"),
                SlackConnection(id=self.connection_id, team_id=self.team_id, slack_team_id="T1", bot_access_token="test-token", status="active", is_deleted=False),
            ])
            task = Task(id=self.task_id, team_id=self.team_id, title="APIを実装", body="認証APIの改善", status="todo", is_deleted=False)
            session.add(task)
            await session.flush()
            session.add(TaskAssignment(
                id=self.assignment_id, task_id=self.task_id, status="ready", task_snapshot=task_snapshot(task),
                candidates=[{"user_id": str(self.user_id), "reason": "認証領域の経験"}, {"user_id": str(self.other_user_id), "reason": "余力あり"}],
            ))
            await session.commit()

    async def asyncTearDown(self):
        if not hasattr(self, "engine"):
            return
        self.session_patch.stop()
        self.post_patch.stop()
        self.dm_patch.stop()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def _state(self):
        async with self.sessions() as session:
            assignment = await session.get(TaskAssignment, self.assignment_id)
            task = await session.get(Task, self.task_id)
            attempts = (await session.execute(select(TaskAssignmentAttempt).order_by(TaskAssignmentAttempt.created_at))).scalars().all()
            return assignment, task, attempts

    async def _dispatch(self):
        await dispatch_next_assignment()
        return (await self._state())[2][-1]

    def _action(self, attempt, action, *, user="U1", team="T1", date=None):
        payload = {
            "type": "block_actions", "team": {"id": team}, "user": {"id": user},
            "channel": {"id": "D1"}, "message": {"ts": "100.1"},
            "actions": [{"action_id": ACTION_PREFIX + action, "value": str(attempt.id)}],
        }
        if date:
            payload["state"] = {"values": {str(attempt.id): {ACTION_PREFIX + "date": {"selected_date": date}}}}
        return payload

    async def _handle(self, payload, *, received_at=None):
        await handle_assignment_event(payload, uuid4(), received_at or datetime.now(timezone.utc))

    async def test_dispatch_sends_only_one_person_and_waits_three_days(self):
        before = datetime.now(timezone.utc)
        attempt = await self._dispatch()
        self.assertFalse(await dispatch_next_assignment())
        assignment, task, attempts = await self._state()
        self.assertEqual(assignment.status, "waiting")
        self.assertIsNone(task.assignee_user_id)
        self.assertEqual(len(attempts), 1)
        self.assertGreaterEqual(attempt.expires_at, before + timedelta(days=3))
        self.post.assert_awaited_once()
        self.assertEqual(self.post.call_args.kwargs["channel_id"], "D1")
        self.assertIn("担当する", str(self.post.call_args.kwargs["blocks"]))

    async def test_timeout_stops_without_notification_or_next_candidate(self):
        attempt = await self._dispatch()
        async with self.sessions() as session:
            record = await session.get(TaskAssignmentAttempt, attempt.id)
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        self.post.reset_mock()
        await expire_assignments()
        self.assertFalse(await dispatch_next_assignment())
        assignment, task, attempts = await self._state()
        self.assertEqual(assignment.status, "expired")
        self.assertEqual(attempts[0].status, "expired")
        self.assertIsNone(task.assignee_user_id)
        self.post.assert_not_awaited()
        await self._handle(self._action(attempt, "accept"))
        self.assertIsNone((await self._state())[1].assignee_user_id)

    async def test_all_declined_stays_unassigned(self):
        attempt = await self._dispatch()
        await self._handle(self._action(attempt, "decline"))
        second = await self._dispatch()
        self.assertEqual(second.user_id, self.other_user_id)
        await self._handle(self._action(second, "decline"))
        self.post.reset_mock()
        await dispatch_next_assignment()
        assignment, task, attempts = await self._state()
        self.assertEqual(assignment.status, "exhausted")
        self.assertIsNone(task.assignee_user_id)
        self.assertTrue(all(a.status == "declined" for a in attempts))
        self.post.assert_not_awaited()

    async def test_accept_updates_task_and_is_idempotent(self):
        attempt = await self._dispatch()
        action = self._action(attempt, "accept", date="2026-10-30")
        await self._handle(action)
        await self._handle(action)
        assignment, task, attempts = await self._state()
        self.assertEqual(assignment.status, "assigned")
        self.assertEqual(task.assignee_user_id, self.user_id)
        self.assertEqual(task.assignee_name, "一郎")
        self.assertEqual(task.due_at, parse_due_date("2026-10-30"))
        self.assertEqual(task.status, "todo")
        self.assertEqual(self.post.await_count, 2)  # initial DM and one confirmation

    async def test_wrong_user_workspace_or_channel_cannot_accept(self):
        attempt = await self._dispatch()
        payloads = [self._action(attempt, "accept", user="intruder"), self._action(attempt, "accept", team="other-team")]
        wrong_channel = self._action(attempt, "accept")
        wrong_channel["channel"] = {"id": "D2"}
        payloads.append(wrong_channel)
        for payload in payloads:
            await self._handle(payload)
        self.assertIsNone((await self._state())[1].assignee_user_id)
        self.assertEqual(self.post.await_count, 1)

    async def test_manual_assignment_is_preserved(self):
        attempt = await self._dispatch()
        async with self.sessions() as session:
            task = await session.get(Task, self.task_id)
            task.assignee_user_id, task.assignee_name = self.other_user_id, "二郎"
            await session.commit()
        await self._handle(self._action(attempt, "accept"))
        assignment, task, _ = await self._state()
        self.assertEqual(assignment.status, "cancelled")
        self.assertEqual(task.assignee_user_id, self.other_user_id)

    async def test_changed_deadline_prevents_accepting_old_offer(self):
        attempt = await self._dispatch()
        async with self.sessions() as session:
            task = await session.get(Task, self.task_id)
            task.due_at = parse_due_date("2026-11-01")
            await session.commit()
        await self._handle(self._action(attempt, "accept"))
        self.assertEqual((await self._state())[0].status, "cancelled")
        self.assertIsNone((await self._state())[1].assignee_user_id)

    async def test_departed_member_cannot_accept(self):
        attempt = await self._dispatch()
        async with self.sessions() as session:
            member = (await session.execute(select(TeamMember).where(TeamMember.user_id == self.user_id))).scalar_one()
            member.left_at = datetime.now(timezone.utc)
            await session.commit()
        await self._handle(self._action(attempt, "accept"))
        self.assertEqual((await self._state())[0].status, "cancelled")
        self.assertIsNone((await self._state())[1].assignee_user_id)

    async def test_chat_proposes_date_but_requires_button_to_assign(self):
        attempt = await self._dispatch()
        payload = {"type": "event_callback", "team_id": "T1", "event": {
            "type": "message", "user": "U1", "channel": "D1", "thread_ts": "100.1", "text": "10月30日なら対応できます",
        }}
        with patch(SERVICE + "generate_assignment_reply", return_value={"answer": "その期限で担当する場合はボタンで確定してください。", "proposed_due_date": "2026-10-30"}):
            await self._handle(payload)
        assignment, task, attempts = await self._state()
        self.assertEqual(assignment.status, "negotiating")
        self.assertIsNone(task.assignee_user_id)
        self.assertIsNone(task.due_at)
        self.assertEqual(attempts[0].proposed_due_at, parse_due_date("2026-10-30"))
        await self._handle(self._action(attempt, "accept"))
        self.assertEqual((await self._state())[1].due_at, parse_due_date("2026-10-30"))

    async def test_timely_queued_response_survives_worker_outage(self):
        attempt = await self._dispatch()
        received_at = datetime.now(timezone.utc) - timedelta(days=2)
        async with self.sessions() as session:
            record = await session.get(TaskAssignmentAttempt, attempt.id)
            record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            await session.commit()
        await expire_assignments()
        self.assertEqual((await self._state())[0].status, "expired")
        await self._handle(self._action(attempt, "accept"), received_at=received_at)
        self.assertEqual((await self._state())[0].status, "assigned")

    async def test_timely_queued_chat_survives_worker_outage(self):
        attempt = await self._dispatch()
        async with self.sessions() as session:
            record = await session.get(TaskAssignmentAttempt, attempt.id)
            record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            await session.commit()
        await expire_assignments()
        payload = {"type": "event_callback", "team_id": "T1", "event": {
            "type": "message", "user": "U1", "channel": "D1", "thread_ts": "100.1", "text": "期限を相談したいです",
        }}
        with patch(SERVICE + "generate_assignment_reply", return_value={"answer": "希望日を教えてください。", "proposed_due_date": ""}):
            await self._handle(payload, received_at=datetime.now(timezone.utc) - timedelta(days=2))
        self.assertEqual((await self._state())[0].status, "negotiating")
        self.assertIsNone((await self._state())[1].assignee_user_id)

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_status_endpoint_requires_team_membership(self, require_member):
        require_member.side_effect = HTTPException(403, "not a team member")
        async with self.sessions() as session:
            with self.assertRaises(HTTPException) as raised:
                await get_task_assignment(self.task_id, SimpleNamespace(), session)
        self.assertEqual(raised.exception.status_code, 403)
        require_member.assert_awaited_once()

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_status_endpoint_returns_progress_without_slack_credentials(self, require_member):
        await self._dispatch()
        async with self.sessions() as session:
            result = await get_task_assignment(self.task_id, SimpleNamespace(), session)
        self.assertEqual(result["assignment"]["status"], "waiting")
        self.assertEqual(result["assignment"]["attempts"][0]["user_name"], "一郎")
        self.assertNotIn("test-token", str(result))

    async def _manual_task(self, **overrides):
        values = {"team_id": self.team_id, "title": "手動タスク", "body": "確認する", "status": "todo", "is_deleted": False}
        values.update(overrides)
        async with self.sessions() as session:
            task = Task(**values)
            session.add(task)
            await session.commit()
            return task.id

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_manual_task_can_be_started_without_sending_inline(self, require_member):
        task_id = await self._manual_task()
        async with self.sessions() as session:
            result = await start_task_assignment(task_id, SimpleNamespace(), session)
            assignment = (await session.execute(select(TaskAssignment).where(TaskAssignment.task_id == task_id))).scalar_one()
            self.assertEqual(assignment.status, "pending")
            self.assertEqual(assignment.round_number, 1)
        self.assertEqual(result, {"started_count": 1, "skipped_count": 0})
        self.post.assert_not_awaited()

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_simultaneous_starts_enqueue_once(self, require_member):
        task_id = await self._manual_task()

        async def start():
            async with self.sessions() as session:
                return await start_task_assignment(task_id, SimpleNamespace(), session)

        results = await asyncio.gather(start(), start())
        self.assertEqual(sum(r["started_count"] for r in results), 1)
        self.assertEqual(sum(r["skipped_count"] for r in results), 1)
        async with self.sessions() as session:
            assignments = (await session.execute(select(TaskAssignment).where(TaskAssignment.task_id == task_id))).scalars().all()
            self.assertEqual(len(assignments), 1)
            self.assertEqual(assignments[0].round_number, 1)

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_bulk_starts_all_eligible_team_tasks_and_skips_active(self, require_member):
        manual_id = await self._manual_task()
        await self._manual_task(status="done")
        await self._manual_task(assignee_user_id=self.user_id, assignee_name="一郎")
        await self._manual_task(assignee_name="担当者あり")
        await self._manual_task(is_deleted=True)
        other_team_id = uuid4()
        async with self.sessions() as session:
            session.add(Team(id=other_team_id, name="別チーム", is_deleted=False))
            await session.commit()
        await self._manual_task(team_id=other_team_id)
        async with self.sessions() as session:
            result = await start_team_assignments(self.team_id, SimpleNamespace(), session)
            assignments = (await session.execute(select(TaskAssignment))).scalars().all()
        self.assertEqual(result, {"started_count": 1, "skipped_count": 1})
        self.assertEqual({a.task_id for a in assignments}, {self.task_id, manual_id})
        self.assertEqual((await self._state())[0].status, "ready")
        self.post.assert_not_awaited()

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_single_active_assignment_is_not_restarted(self, require_member):
        async with self.sessions() as session:
            result = await start_task_assignment(self.task_id, SimpleNamespace(), session)
        self.assertEqual(result, {"started_count": 0, "skipped_count": 1})
        self.assertEqual((await self._state())[0].round_number, 1)

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_assigned_and_completed_tasks_cannot_be_started(self, require_member):
        for overrides in [{"status": "done"}, {"assignee_user_id": self.user_id}]:
            task_id = await self._manual_task(**overrides)
            async with self.sessions() as session:
                with self.assertRaises(HTTPException) as raised:
                    await start_task_assignment(task_id, SimpleNamespace(), session)
                self.assertEqual(raised.exception.status_code, 409)
                self.assertIsNone((await session.execute(select(TaskAssignment).where(TaskAssignment.task_id == task_id))).scalar_one_or_none())

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_start_requires_slack_connection(self, require_member):
        task_id = await self._manual_task()
        async with self.sessions() as session:
            connection = await session.get(SlackConnection, self.connection_id)
            connection.status = "revoked"
            await session.commit()
            with self.assertRaises(HTTPException) as raised:
                await start_task_assignment(task_id, SimpleNamespace(), session)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIsNone((await session.execute(select(TaskAssignment).where(TaskAssignment.task_id == task_id))).scalar_one_or_none())

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_start_endpoints_require_team_membership(self, require_member):
        require_member.side_effect = HTTPException(403, "not a team member")
        async with self.sessions() as session:
            for function, identifier in [(start_task_assignment, self.task_id), (start_team_assignments, self.team_id)]:
                with self.assertRaises(HTTPException) as raised:
                    await function(identifier, SimpleNamespace(), session)
                self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual((await self._state())[0].status, "ready")

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_manual_restart_preserves_history_and_can_offer_previous_candidate(self, require_member):
        previous = await self._dispatch()
        async with self.sessions() as session:
            attempt = await session.get(TaskAssignmentAttempt, previous.id)
            attempt.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            previous_received_at = attempt.expires_at - timedelta(seconds=1)
            await session.commit()
        await expire_assignments()
        async with self.sessions() as session:
            result = await start_task_assignment(self.task_id, SimpleNamespace(), session)
        self.assertEqual(result["started_count"], 1)
        ranking = [{"user_id": str(self.user_id), "reason": "適任"}, {"user_id": str(self.other_user_id), "reason": "候補"}]
        with patch(SERVICE + "rank_assignment_candidates", return_value=ranking):
            await select_next_assignment()
        current = await self._dispatch()
        self.assertEqual(current.user_id, self.user_id)
        self.assertEqual(current.round_number, 2)
        self.assertEqual(len((await self._state())[2]), 2)
        async with self.sessions() as session:
            response = await get_task_assignment(self.task_id, SimpleNamespace(), session)
            self.assertFalse(response["can_start"])
            self.assertEqual(len(response["assignment"]["attempts"]), 1)
            attempt = await session.get(TaskAssignmentAttempt, current.id)
            attempt.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        await expire_assignments()
        await self._handle(self._action(previous, "accept"), received_at=previous_received_at)
        self.assertIsNone((await self._state())[1].assignee_user_id)
        self.assertEqual((await self._state())[0].status, "expired")

    @patch("app.api.task_assignments.require_team_member", new_callable=AsyncMock)
    async def test_late_send_result_cannot_overwrite_a_restarted_round(self, require_member):
        async def complete_after_restart(**kwargs):
            async with self.sessions() as session:
                assignment = await session.get(TaskAssignment, self.assignment_id)
                assignment.claimed_at = datetime.now(timezone.utc) - timedelta(minutes=6)
                await session.commit()
            await expire_assignments()
            async with self.sessions() as session:
                await start_task_assignment(self.task_id, SimpleNamespace(), session)
            return SimpleNamespace(slack_ts="100.1", channel_id="D1")

        self.post.side_effect = complete_after_restart
        await dispatch_next_assignment()
        assignment, task, attempts = await self._state()
        self.assertEqual(assignment.round_number, 2)
        self.assertEqual(assignment.status, "pending")
        self.assertEqual(attempts[0].status, "failed")
        self.assertIsNone(task.assignee_user_id)

    async def test_stopped_send_is_not_automatically_resent(self):
        attempt = await self._dispatch()
        async with self.sessions() as session:
            assignment = await session.get(TaskAssignment, self.assignment_id)
            assignment.status = "sending"
            assignment.claimed_at = datetime.now(timezone.utc) - timedelta(minutes=6)
            record = await session.get(TaskAssignmentAttempt, attempt.id)
            record.status = "sending"
            await session.commit()
        self.post.reset_mock()
        await expire_assignments()
        await dispatch_next_assignment()
        self.assertEqual((await self._state())[0].status, "failed")
        self.post.assert_not_awaited()

    async def test_ranking_uses_task_load_and_domain_evidence(self):
        async with self.sessions() as session:
            assignment = await session.get(TaskAssignment, self.assignment_id)
            assignment.status = "pending"
            session.add(Task(team_id=self.team_id, title="OAuth認証", body="認証基盤を改善", status="in_progress", assignee_user_id=self.user_id, is_deleted=False))
            await session.commit()
        ranked = [{"user_id": str(self.other_user_id), "reason": "負荷が低い"}, {"user_id": str(self.user_id), "reason": "認証経験"}]
        with patch(SERVICE + "rank_assignment_candidates", return_value=ranked) as rank:
            await select_next_assignment()
        first = next(m for m in rank.call_args.kwargs["members"] if m["user_id"] == str(self.user_id))
        self.assertEqual(first["open_task_count"], 1)
        self.assertEqual(first["in_progress_count"], 1)
        self.assertEqual(first["domain_evidence"][0]["title"], "OAuth認証")
        self.assertEqual((await self._state())[0].candidates, ranked)

    async def test_minutes_generation_enqueues_only_new_unassigned_tasks_once(self):
        from app.api.tasks import generate_team_tasks_for_minutes
        minutes_id = uuid4()
        async with self.sessions() as session:
            meeting = Meeting(team_id=self.team_id, title="開発会議", status="ended", started_at=datetime.now(timezone.utc), is_deleted=False)
            session.add(meeting)
            await session.flush()
            session.add(MeetingMinutes(id=minutes_id, meeting_id=meeting.id, body="新しいタスク", source_text="会話", is_deleted=False))
            await session.commit()
        actions = [
            {"action": "create", "title": "新規API", "body": "設計する", "status": "todo"},
            {"action": "create", "title": "担当済みAPI", "body": "実装する", "status": "todo", "assignee_user_id": str(self.user_id)},
        ]
        with patch("app.api.tasks.generate_task_actions_from_minutes", return_value=actions) as generate:
            async with self.sessions() as session:
                first = await generate_team_tasks_for_minutes(team_id=self.team_id, minutes_id=minutes_id, session=session)
                second = await generate_team_tasks_for_minutes(team_id=self.team_id, minutes_id=minutes_id, session=session)
        self.assertEqual(first.created_count, 2)
        self.assertEqual(second.created_count, 0)
        generate.assert_called_once()
        async with self.sessions() as session:
            rows = (await session.execute(select(TaskAssignment).join(Task).where(Task.source_minutes_id == minutes_id))).scalars().all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status, "pending")

    @patch("app.api.task_assignments.get_settings", return_value=SimpleNamespace(slack_signing_secret=SECRET))
    async def test_slack_retry_is_deduplicated_before_processing(self, settings):
        attempt = await self._dispatch()
        payload = self._action(attempt, "accept")
        async with self.sessions() as session:
            await receive_slack_callback(signed_request(payload, form=True), session)
            await receive_slack_callback(signed_request(payload, form=True), session)
            events = (await session.execute(select(TaskAssignmentEvent))).scalars().all()
            self.assertEqual(len(events), 1)
        self.assertTrue(await process_next_event())
        self.assertFalse(await process_next_event())
        self.assertEqual((await self._state())[0].status, "assigned")

    @patch("app.api.task_assignments.get_settings", return_value=SimpleNamespace(slack_signing_secret=SECRET))
    async def test_challenge_and_invalid_signature_do_not_queue_work(self, settings):
        async with self.sessions() as session:
            result = await receive_slack_callback(signed_request({"type": "url_verification", "challenge": "test-challenge"}), session)
            self.assertEqual(result, {"challenge": "test-challenge"})
            with self.assertRaises(HTTPException) as raised:
                await receive_slack_callback(signed_request({"type": "url_verification"}, timestamp=int(time.time()) - 301), session)
            self.assertEqual(raised.exception.status_code, 401)
            self.assertEqual((await session.execute(select(TaskAssignmentEvent))).scalars().all(), [])


if __name__ == "__main__":
    unittest.main()
