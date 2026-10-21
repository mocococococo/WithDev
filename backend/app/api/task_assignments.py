import hashlib
import json
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, verify_firebase_user
from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.task import Task
from app.models.task_assignment import TaskAssignment, TaskAssignmentAttempt, TaskAssignmentEvent
from app.models.user import User
from app.services.slack_assignment_transport import ACTION_PREFIX, verify_slack_signature
from app.services.team_access_service import require_team_member
from app.services.slack_post_service import SlackConnectionNotFoundError, get_active_slack_connection
from app.services.task_assignment_service import TERMINAL_STATUSES, task_is_available

router = APIRouter()


async def _require_slack_connection(session: AsyncSession, team_id: UUID):
    try:
        await get_active_slack_connection(session=session, team_id=team_id)
    except SlackConnectionNotFoundError as exc:
        raise HTTPException(409, "チームのSlack連携を設定してください。") from exc


async def _enqueue_assignment(session: AsyncSession, task_id: UUID) -> bool:
    # Insert first, then lock assignment -> task, matching the callback worker's lock order.
    # ON CONFLICT also serializes simultaneous starts when no assignment exists yet.
    created_id = (await session.execute(insert(TaskAssignment).values(
        task_id=task_id, status="pending", round_number=1,
    ).on_conflict_do_nothing(index_elements=["task_id"]).returning(TaskAssignment.id))).scalar_one_or_none()
    assignment = (await session.execute(select(TaskAssignment).where(
        TaskAssignment.task_id == task_id,
    ).with_for_update().execution_options(populate_existing=True))).scalar_one()
    task = (await session.execute(select(Task).where(
        Task.id == task_id,
    ).with_for_update().execution_options(populate_existing=True))).scalar_one()
    if not task_is_available(task):
        if created_id:
            assignment.status = "cancelled"
        return False
    if created_id:
        return True
    if assignment.status not in TERMINAL_STATUSES:
        return False
    # Keep old attempts for history, but old Slack buttons cannot affect this new round.
    assignment.round_number += 1
    assignment.status = "pending"
    assignment.candidates = assignment.task_snapshot = None
    assignment.error = assignment.claimed_at = None
    return True


@router.post("/tasks/{task_id}/assignment/start")
async def start_task_assignment(
    task_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
):
    task = await session.get(Task, task_id)
    if not task or task.is_deleted:
        raise HTTPException(404, "task not found")
    await require_team_member(session=session, auth_user=auth_user, team_id=task.team_id)
    if not task_is_available(task):
        raise HTTPException(409, "未担当・未完了のタスクのみ打診できます。")
    await _require_slack_connection(session, task.team_id)
    started = await _enqueue_assignment(session, task_id)
    await session.commit()
    return {"started_count": int(started), "skipped_count": int(not started)}


@router.post("/teams/{team_id}/assignments/start")
async def start_team_assignments(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
):
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)
    await _require_slack_connection(session, team_id)
    task_ids = (await session.execute(select(Task.id).where(
        Task.team_id == team_id, Task.is_deleted.is_(False), Task.status != "done",
        Task.assignee_user_id.is_(None), or_(Task.assignee_name.is_(None), Task.assignee_name == ""),
    ).order_by(Task.id))).scalars().all()
    started_count = 0
    for task_id in task_ids:
        started_count += int(await _enqueue_assignment(session, task_id))
    await session.commit()
    return {"started_count": started_count, "skipped_count": len(task_ids) - started_count}


@router.post("/slack/events")
@router.post("/slack/interactions")
async def receive_slack_callback(request: Request, session: AsyncSession = Depends(get_db_session)):
    secret = get_settings().slack_signing_secret
    if not secret:
        raise HTTPException(503, "Slack signing secret is not configured")
    body = await request.body()
    if not verify_slack_signature(
        body=body, timestamp=request.headers.get("x-slack-request-timestamp", ""),
        signature=request.headers.get("x-slack-signature", ""), secret=secret,
    ):
        raise HTTPException(401, "invalid Slack signature")
    try:
        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            payload = json.loads(parse_qs(body.decode())["payload"][0])
        else:
            payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("expected object")
    except (ValueError, KeyError, IndexError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "invalid Slack payload") from exc
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}
    if payload.get("type") == "block_actions":
        actions = payload.get("actions")
        if not isinstance(actions, list) or len(actions) != 1 or not isinstance(actions[0], dict):
            return Response(status_code=200)
        if actions[0].get("action_id") not in {
            ACTION_PREFIX + name for name in ("accept", "decline", "consult", "date")
        }:
            return Response(status_code=200)
    elif payload.get("type") == "event_callback":
        event = payload.get("event")
        if (not isinstance(event, dict) or event.get("type") != "message"
                or event.get("channel_type") != "im" or event.get("subtype") or event.get("bot_id")
                or not event.get("user") or not isinstance(event.get("text"), str) or not event["text"].strip()):
            return Response(status_code=200)
    else:
        return Response(status_code=200)
    external_id = ("event:" + payload["event_id"] if isinstance(payload.get("event_id"), str)
                   else "action:" + hashlib.sha256(body).hexdigest())
    # Durable insert before the acknowledgement; model and Slack API work happen in the worker.
    await session.execute(insert(TaskAssignmentEvent).values(
        external_id=external_id, payload=payload, status="pending",
    ).on_conflict_do_nothing(index_elements=["external_id"]))
    await session.commit()
    return Response(status_code=200)


@router.get("/tasks/{task_id}/assignment")
async def get_task_assignment(
    task_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
):
    task = await session.get(Task, task_id)
    if not task or task.is_deleted:
        raise HTTPException(404, "task not found")
    await require_team_member(session=session, auth_user=auth_user, team_id=task.team_id)
    assignment = (await session.execute(select(TaskAssignment).where(
        TaskAssignment.task_id == task_id,
    ))).scalar_one_or_none()
    if not assignment:
        return {"assignment": None, "can_start": task_is_available(task)}
    rows = (await session.execute(select(TaskAssignmentAttempt, User.display_name).join(
        User, User.id == TaskAssignmentAttempt.user_id,
    ).where(
        TaskAssignmentAttempt.assignment_id == assignment.id,
        TaskAssignmentAttempt.round_number == assignment.round_number,
    ).order_by(TaskAssignmentAttempt.created_at))).all()
    return {"can_start": task_is_available(task) and assignment.status in TERMINAL_STATUSES, "assignment": {
        "status": assignment.status, "error": assignment.error,
        "attempts": [{
            "user_name": name, "status": attempt.status,
            "expires_at": attempt.expires_at, "proposed_due_at": attempt.proposed_due_at,
        } for attempt, name in rows],
    }}
