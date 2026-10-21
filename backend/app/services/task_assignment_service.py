import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from starlette.concurrency import run_in_threadpool

from app.db.session import AsyncSessionLocal
from app.models.slack import SlackConnection
from app.models.task import Task
from app.models.task_assignment import TaskAssignment, TaskAssignmentAttempt, TaskAssignmentEvent
from app.models.team import Team, TeamMember
from app.models.user import User
from app.services.assignment_agent_service import rank_assignment_candidates, generate_assignment_reply
from app.services.slack_assignment_transport import (
    ACTION_PREFIX, assignment_blocks, open_assignment_dm, parse_due_date, TOKYO,
)
from app.services.slack_post_service import get_active_slack_connection
from app.services.slack_service import post_message

logger = logging.getLogger(__name__)
RESPONSE_WINDOW = timedelta(days=3)
CLAIM_TIMEOUT = timedelta(minutes=5)
OPEN_STATUSES = {"waiting", "negotiating"}
TERMINAL_STATUSES = {"assigned", "expired", "exhausted", "cancelled", "failed"}


def utcnow():
    return datetime.now(timezone.utc)


def task_snapshot(task: Task) -> dict:
    return {
        "title": task.title, "body": task.body,
        "due_at": task.due_at.isoformat() if task.due_at else None,
    }


def task_is_available(task: Task) -> bool:
    return not task.is_deleted and task.status != "done" and not task.assignee_user_id and not task.assignee_name


def invalidate_if_changed(assignment, attempt, task, now: datetime) -> bool:
    if not task_is_available(task) or assignment.task_snapshot != task_snapshot(task):
        assignment.status = attempt.status = "cancelled"
        return True
    if attempt.expires_at and attempt.expires_at <= now:
        assignment.status = attempt.status = "expired"
        return True
    return False


async def _members_and_workload(session, team_id: UUID) -> list[dict]:
    users = (await session.execute(
        select(User).join(TeamMember, TeamMember.user_id == User.id).where(
            TeamMember.team_id == team_id, TeamMember.left_at.is_(None), User.is_deleted.is_(False),
        ).order_by(User.id)
    )).scalars().all()
    tasks = (await session.execute(
        select(Task).where(Task.team_id == team_id, Task.is_deleted.is_(False)).order_by(Task.updated_at.desc())
    )).scalars().all()
    pending = (await session.execute(
        select(TaskAssignmentAttempt.user_id).join(
            TaskAssignment, TaskAssignment.id == TaskAssignmentAttempt.assignment_id,
        ).join(Task, Task.id == TaskAssignment.task_id).where(
            Task.team_id == team_id, TaskAssignmentAttempt.status.in_(OPEN_STATUSES),
        )
    )).scalars().all()
    members = []
    for user in users:
        assigned = [task for task in tasks if task.assignee_user_id == user.id]
        active = [task for task in assigned if task.status != "done"]
        members.append({
            "user_id": str(user.id), "name": user.display_name,
            "open_task_count": len(active),
            "in_progress_count": sum(task.status == "in_progress" for task in active),
            "pending_offer_count": pending.count(user.id),
            "open_tasks": [{"title": task.title, "due_at": task.due_at} for task in active[:30]],
            "domain_evidence": [{"title": task.title, "body": task.body[:500]} for task in assigned[:15]],
        })
    return members


async def select_next_assignment() -> bool:
    """Claim a ranking job, release the DB transaction, then call the agent."""
    async with AsyncSessionLocal() as session:
        assignment = (await session.execute(
            select(TaskAssignment).where(TaskAssignment.status == "pending")
            .order_by(TaskAssignment.created_at).with_for_update(skip_locked=True).limit(1)
        )).scalar_one_or_none()
        if assignment is None:
            return False
        task = await session.get(Task, assignment.task_id)
        team = await session.get(Team, task.team_id)
        if not task_is_available(task) or team.is_deleted:
            assignment.status = "cancelled"
            await session.commit()
            return True
        members = await _members_and_workload(session, task.team_id)
        snapshot = task_snapshot(task)
        claimed_at = utcnow()
        assignment.status = "selecting"
        assignment.claimed_at = claimed_at
        assignment.task_snapshot = snapshot
        assignment_id = assignment.id
        await session.commit()
    try:
        candidates = await run_in_threadpool(rank_assignment_candidates, task=snapshot, members=members)
        error = None
    except Exception:
        logger.exception("Assignment candidate selection failed assignment_id=%s", assignment_id)
        candidates, error = [], "担当候補の選定に失敗しました。"
    async with AsyncSessionLocal() as session:
        assignment = (await session.execute(
            select(TaskAssignment).where(TaskAssignment.id == assignment_id).with_for_update()
        )).scalar_one()
        if assignment.status == "selecting" and assignment.claimed_at == claimed_at:
            assignment.candidates = candidates
            assignment.error = error
            assignment.status = "failed" if error else ("ready" if candidates else "exhausted")
            await session.commit()
    return True


async def dispatch_next_assignment() -> bool:
    async with AsyncSessionLocal() as session:
        assignment = (await session.execute(
            select(TaskAssignment).where(TaskAssignment.status == "ready")
            .order_by(TaskAssignment.created_at).with_for_update(skip_locked=True).limit(1)
        )).scalar_one_or_none()
        if assignment is None:
            return False
        task = await session.get(Task, assignment.task_id)
        team = await session.get(Team, task.team_id)
        if not task_is_available(task) or team.is_deleted or task_snapshot(task) != assignment.task_snapshot:
            assignment.status = "cancelled"
            await session.commit()
            return True
        attempted = set((await session.execute(
            select(TaskAssignmentAttempt.user_id).where(
                TaskAssignmentAttempt.assignment_id == assignment.id,
                TaskAssignmentAttempt.round_number == assignment.round_number,
            )
        )).scalars().all())
        candidate = None
        for item in assignment.candidates or []:
            user_id = UUID(item["user_id"])
            if user_id not in attempted:
                membership = (await session.execute(select(TeamMember).where(
                    TeamMember.team_id == task.team_id, TeamMember.user_id == user_id,
                    TeamMember.left_at.is_(None),
                ))).scalar_one_or_none()
                user = await session.get(User, user_id)
                if membership and user and not user.is_deleted:
                    candidate = user
                    break
        if candidate is None:
            assignment.status = "exhausted"
            await session.commit()
            return True
        try:
            connection = await get_active_slack_connection(session=session, team_id=task.team_id)
        except Exception:
            assignment.status, assignment.error = "failed", "Slackが接続されていません。"
            await session.commit()
            return True
        attempt = TaskAssignmentAttempt(
            id=uuid4(), assignment_id=assignment.id, user_id=candidate.id,
            round_number=assignment.round_number,
            connection_id=connection.id, status="sending", proposed_due_at=task.due_at, history=[],
        )
        session.add(attempt)
        assignment.status, assignment.claimed_at = "sending", utcnow()
        token, email = connection.bot_access_token, candidate.email
        due_label = task.due_at.astimezone(TOKYO).date().isoformat() if task.due_at else "未設定"
        text = f"「{task.title}」を担当できますか？\n\n{task.body[:1800]}\n\n希望期限：{due_label}"
        attempt_id = attempt.id
        due_at = task.due_at
        await session.commit()
    try:
        slack_user_id, channel_id = await open_assignment_dm(token=token, email=email)
        # Persist the recipient before sending, so callbacks can only match the intended person.
        async with AsyncSessionLocal() as session:
            attempt = await session.get(TaskAssignmentAttempt, attempt_id)
            assignment = (await session.execute(select(TaskAssignment).where(
                TaskAssignment.id == attempt.assignment_id,
            ).with_for_update())).scalar_one()
            if (attempt.round_number != assignment.round_number
                    or assignment.status != "sending" or attempt.status != "sending"):
                return True
            attempt.slack_user_id, attempt.channel_id = slack_user_id, channel_id
            await session.commit()
        result = await post_message(
            bot_access_token=token, channel_id=channel_id, text=text,
            blocks=assignment_blocks(text=text, attempt_id=attempt_id, due_at=due_at),
            client_msg_id=str(attempt_id),
        )
    except Exception:
        logger.exception("Assignment DM failed attempt_id=%s", attempt_id)
        async with AsyncSessionLocal() as session:
            attempt = await session.get(TaskAssignmentAttempt, attempt_id)
            assignment = (await session.execute(select(TaskAssignment).where(
                TaskAssignment.id == attempt.assignment_id,
            ).with_for_update())).scalar_one()
            if attempt.round_number != assignment.round_number or assignment.status != "sending":
                return True
            attempt.status = "failed"
            assignment.status, assignment.error = "failed", "Slackへの打診に失敗しました。再送は行いません。"
            await session.commit()
        return True
    async with AsyncSessionLocal() as session:
        attempt = await session.get(TaskAssignmentAttempt, attempt_id)
        assignment = (await session.execute(select(TaskAssignment).where(
            TaskAssignment.id == attempt.assignment_id,
        ).with_for_update())).scalar_one()
        if (attempt.round_number != assignment.round_number
                or assignment.status != "sending" or attempt.status != "sending"):
            return True
        attempt.slack_ts = result.slack_ts
        attempt.status = assignment.status = "waiting"
        attempt.expires_at = utcnow() + RESPONSE_WINDOW
        attempt.history = [{"role": "assistant", "content": text}]
        assignment.error = None
        await session.commit()
    return True


async def _find_attempt(session, payload: dict, received_at: datetime):
    team_id = (payload.get("team") or {}).get("id") or payload.get("team_id")
    if payload.get("type") == "block_actions":
        action = payload["actions"][0]
        value = action.get("value") or action.get("block_id")
        try:
            attempt_id = UUID(value)
        except (ValueError, TypeError, AttributeError):
            return None
        attempt = await session.get(TaskAssignmentAttempt, attempt_id)
        if not attempt:
            return None
        connection = await session.get(SlackConnection, attempt.connection_id)
        message = payload.get("message", {})
        if (
            connection.slack_team_id != team_id
            or (payload.get("user") or {}).get("id") != attempt.slack_user_id
            or (payload.get("channel") or {}).get("id") != attempt.channel_id
            or (message.get("thread_ts") or message.get("ts")) != attempt.slack_ts
        ):
            return None
        return attempt
    event = payload.get("event", {})
    statement = select(TaskAssignmentAttempt).join(
        SlackConnection, SlackConnection.id == TaskAssignmentAttempt.connection_id,
    ).where(
        SlackConnection.slack_team_id == team_id,
        TaskAssignmentAttempt.slack_user_id == event.get("user"),
        TaskAssignmentAttempt.channel_id == event.get("channel"),
        or_(
            TaskAssignmentAttempt.status.in_(OPEN_STATUSES),
            and_(TaskAssignmentAttempt.status == "expired", TaskAssignmentAttempt.expires_at >= received_at),
        ),
    )
    if event.get("thread_ts"):
        statement = statement.where(TaskAssignmentAttempt.slack_ts == event["thread_ts"])
    attempts = (await session.execute(statement)).scalars().all()
    # An unthreaded reply is only unambiguous when exactly one task is being discussed.
    return attempts[0] if len(attempts) == 1 else None


async def _locked_context(session, attempt_id: UUID, received_at: datetime):
    attempt = await session.get(TaskAssignmentAttempt, attempt_id)
    assignment = (await session.execute(
        select(TaskAssignment).where(TaskAssignment.id == attempt.assignment_id).with_for_update()
    )).scalar_one()
    task = (await session.execute(select(Task).where(Task.id == assignment.task_id).with_for_update())).scalar_one()
    await session.refresh(attempt)
    connection = await session.get(SlackConnection, attempt.connection_id)
    team = await session.get(Team, task.team_id)
    member = (await session.execute(select(TeamMember).where(
        TeamMember.team_id == task.team_id, TeamMember.user_id == attempt.user_id, TeamMember.left_at.is_(None),
    ))).scalar_one_or_none()
    user = await session.get(User, attempt.user_id)
    if attempt.round_number != assignment.round_number:
        return None
    # A callback durably received before the deadline remains valid after a worker outage.
    if (assignment.status == attempt.status == "expired" and attempt.expires_at
            and received_at <= attempt.expires_at):
        assignment.status = attempt.status = "waiting"
    if attempt.status not in OPEN_STATUSES or assignment.status not in OPEN_STATUSES:
        return None
    if (not member or not user or user.is_deleted or team.is_deleted
            or connection.is_deleted or connection.status != "active"):
        assignment.status = attempt.status = "cancelled"
        return None
    if invalidate_if_changed(assignment, attempt, task, received_at):
        return None
    return assignment, attempt, task, connection, user


def _selected_due_date(payload: dict):
    action = payload["actions"][0]
    if action.get("action_id") == ACTION_PREFIX + "date":
        return action.get("selected_date")
    for values in (payload.get("state") or {}).get("values", {}).values():
        picker = values.get(ACTION_PREFIX + "date")
        if picker and picker.get("selected_date"):
            return picker["selected_date"]
    return None


async def handle_assignment_event(payload: dict, event_id: UUID, received_at: datetime):
    reply = None
    async with AsyncSessionLocal() as session:
        found = await _find_attempt(session, payload, received_at)
        if found is None:
            return
        attempt_id = found.id
        context = await _locked_context(session, attempt_id, received_at)
        if context is None:
            await session.commit()
            return
        assignment, attempt, task, connection, user = context
        if payload.get("type") == "block_actions":
            action = payload["actions"][0].get("action_id", "")
            selected_date = _selected_due_date(payload)
            if selected_date:
                attempt.proposed_due_at = parse_due_date(selected_date)
            if action == ACTION_PREFIX + "date":
                await session.commit()
                return
            if action == ACTION_PREFIX + "accept":
                task.assignee_user_id, task.assignee_name = user.id, user.display_name
                task.due_at = attempt.proposed_due_at
                assignment.status, attempt.status = "assigned", "accepted"
                if task.roadmap:
                    from app.api.tasks import _mark_roadmap_pending
                    _mark_roadmap_pending(task.roadmap)
                due_label = task.due_at.astimezone(TOKYO).date().isoformat() if task.due_at else "未設定"
                reply = f"担当を{user.display_name}さん、期限を{due_label}としてWithDevに反映しました。"
            elif action == ACTION_PREFIX + "decline":
                attempt.status, assignment.status = "declined", "ready"
                reply = "回答ありがとうございます。今回は辞退として受け付けました。"
            elif action == ACTION_PREFIX + "consult":
                assignment.status = attempt.status = "negotiating"
                reply = "希望する期限を教えてください。日付を選択するか、このスレッドに返信できます。合意できる期限を選んだら「担当する」で確定してください。"
            else:
                return
            token, channel_id, thread_ts = connection.bot_access_token, attempt.channel_id, attempt.slack_ts
            blocks = (assignment_blocks(text=reply, attempt_id=attempt_id, due_at=attempt.proposed_due_at)
                      if attempt.status in OPEN_STATUSES else None)
            if attempt.status in OPEN_STATUSES:
                attempt.expires_at = utcnow() + RESPONSE_WINDOW
            await session.commit()
        else:
            message = payload["event"]["text"][:2000]
            snapshot, history = assignment.task_snapshot, attempt.history or []
            # No transaction is kept open while waiting for the model.
            await session.rollback()
            generated = await run_in_threadpool(
                generate_assignment_reply, task=snapshot, history=history, message=message, now=utcnow(),
            )
            context = await _locked_context(session, attempt_id, received_at)
            if context is None:
                await session.commit()
                return
            assignment, attempt, task, connection, user = context
            proposed_date = generated.get("proposed_due_date")
            if proposed_date:
                try:
                    attempt.proposed_due_at = parse_due_date(proposed_date)
                except (TypeError, ValueError):
                    pass
            reply = generated["answer"]
            if attempt.proposed_due_at:
                reply += "\n\n確定する期限：" + attempt.proposed_due_at.astimezone(TOKYO).date().isoformat()
            assignment.status = attempt.status = "negotiating"
            attempt.history = ((attempt.history or []) + [
                {"role": "user", "content": message}, {"role": "assistant", "content": reply},
            ])[-24:]
            attempt.expires_at = utcnow() + RESPONSE_WINDOW
            token, channel_id, thread_ts = connection.bot_access_token, attempt.channel_id, attempt.slack_ts
            blocks = assignment_blocks(text=reply, attempt_id=attempt_id, due_at=attempt.proposed_due_at)
            await session.commit()
    if reply:
        await post_message(
            bot_access_token=token, channel_id=channel_id, thread_ts=thread_ts,
            text=reply, blocks=blocks, client_msg_id=str(event_id),
        )


async def process_next_event() -> bool:
    async with AsyncSessionLocal() as session:
        event = (await session.execute(
            select(TaskAssignmentEvent).where(TaskAssignmentEvent.status == "pending")
            .order_by(TaskAssignmentEvent.created_at, TaskAssignmentEvent.id)
            .with_for_update(skip_locked=True).limit(1)
        )).scalar_one_or_none()
        if event is None:
            return False
        event.status = "processing"
        event_id, payload, received_at = event.id, event.payload, event.created_at
        await session.commit()
    try:
        await handle_assignment_event(payload, event_id, received_at)
        status = "done"
    except Exception:
        logger.exception("Assignment callback failed event_id=%s", event_id)
        status = "failed"
    async with AsyncSessionLocal() as session:
        event = await session.get(TaskAssignmentEvent, event_id)
        event.status = status
        await session.commit()
    return True


async def expire_assignments():
    """Timeouts never send reminders, try another candidate, or modify the task."""
    now = utcnow()
    async with AsyncSessionLocal() as session:
        assignments = (await session.execute(select(TaskAssignment).where(
            TaskAssignment.status.not_in(TERMINAL_STATUSES),
        ).with_for_update(skip_locked=True))).scalars().all()
        for assignment in assignments:
            if assignment.status in {"selecting", "sending"}:
                if assignment.claimed_at and assignment.claimed_at < now - CLAIM_TIMEOUT:
                    # A send may have succeeded before a worker stopped. Do not resend it.
                    assignment.status = "failed"
                    assignment.error = "処理が中断されました。重複送信を避けるため自動再送は行いません。"
                else:
                    continue
            task = await session.get(Task, assignment.task_id)
            team = await session.get(Team, task.team_id)
            attempts = (await session.execute(select(TaskAssignmentAttempt).where(
                TaskAssignmentAttempt.assignment_id == assignment.id,
                TaskAssignmentAttempt.status.in_(OPEN_STATUSES | {"sending"}),
            ))).scalars().all()
            if team.is_deleted or not task_is_available(task):
                assignment.status = "cancelled"
                for attempt in attempts:
                    attempt.status = "cancelled"
            for attempt in attempts:
                if assignment.status == "failed":
                    attempt.status = "failed"
                else:
                    invalidate_if_changed(assignment, attempt, task, now)
        await session.commit()
    # Claimed callbacks are never replayed: their outbound send may already have succeeded.
    async with AsyncSessionLocal() as session:
        events = (await session.execute(select(TaskAssignmentEvent).where(
            TaskAssignmentEvent.status == "processing", TaskAssignmentEvent.updated_at < now - CLAIM_TIMEOUT,
        ).with_for_update(skip_locked=True))).scalars().all()
        for event in events:
            event.status = "failed"
        await session.commit()
