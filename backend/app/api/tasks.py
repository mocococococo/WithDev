from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.auth import AuthenticatedUser, verify_firebase_user
from app.db.session import get_db_session
from app.models.meeting import Meeting
from app.models.minutes import MeetingMinutes
from app.models.task import Task
from app.models.team import TeamMember
from app.models.user import User
from app.services.tasks_service import (
    TaskGenerationError,
    generate_task_actions_from_minutes,
)
from app.services.team_access_service import require_team_member


router = APIRouter()
VALID_TASK_STATUSES = {"todo", "doing", "done"}


class TaskBody(BaseModel):
    id: UUID
    team_id: UUID
    source_minutes_id: UUID | None
    title: str
    body: str
    assignee_user_id: UUID | None
    assignee_name: str | None
    status: str
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    tasks: list[TaskBody]


class TaskResponse(BaseModel):
    task: TaskBody


class TaskGenerateRequest(BaseModel):
    minutes_id: UUID


class TaskGenerateResponse(BaseModel):
    tasks: list[TaskBody]
    created_count: int
    updated_count: int
    deleted_count: int


class TaskUpdateRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    assignee_user_id: UUID | None = None
    assignee_name: str | None = None
    status: str | None = None
    due_at: datetime | None = None


class TeamMemberBody(BaseModel):
    user_id: UUID
    display_name: str
    email: str
    role: str


class TeamMemberListResponse(BaseModel):
    members: list[TeamMemberBody]


@router.get("/teams/{team_id}/members", response_model=TeamMemberListResponse)
async def list_team_members(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TeamMemberListResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)
    members = await _get_active_team_members(session=session, team_id=team_id)
    return TeamMemberListResponse(members=[_team_member_body(user, role) for user, role in members])


@router.get("/teams/{team_id}/tasks", response_model=TaskListResponse)
async def list_team_tasks(
    team_id: UUID,
    assignee: str | None = None,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskListResponse:
    user, _team = await require_team_member(
        session=session,
        auth_user=auth_user,
        team_id=team_id,
    )

    statement = select(Task).where(Task.team_id == team_id, Task.is_deleted.is_(False))
    if assignee is not None:
        if assignee != "me":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="unsupported assignee filter",
            )
        statement = statement.where(Task.assignee_user_id == user.id)

    result = await session.execute(statement.order_by(Task.updated_at.desc(), Task.created_at.desc()))
    return TaskListResponse(tasks=[_task_body(task) for task in result.scalars().all()])


@router.post("/teams/{team_id}/tasks/generate", response_model=TaskGenerateResponse)
async def generate_team_tasks(
    team_id: UUID,
    request: TaskGenerateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskGenerateResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)

    minutes = await _get_accessible_minutes(
        session=session,
        team_id=team_id,
        minutes_id=request.minutes_id,
    )
    members = await _get_active_team_members(session=session, team_id=team_id)
    member_map = {user.id: user for user, _role in members}
    existing_tasks = await _get_team_tasks(session=session, team_id=team_id)

    try:
        actions = await run_in_threadpool(
            generate_task_actions_from_minutes,
            minutes_body=minutes.body,
            existing_tasks=[_task_prompt_body(task) for task in existing_tasks],
            team_members=[_member_prompt_body(user, role) for user, role in members],
        )
    except TaskGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to generate tasks",
        ) from exc

    existing_task_map = {task.id: task for task in existing_tasks}
    created_count = 0
    updated_count = 0
    deleted_count = 0

    for action in actions:
        action_name = str(action.get("action") or "").strip().lower()
        if action_name == "create":
            task = _create_task_from_action(
                action=action,
                team_id=team_id,
                source_minutes_id=minutes.id,
                member_map=member_map,
            )
            session.add(task)
            created_count += 1
        elif action_name == "update":
            task = _find_action_task(action=action, existing_task_map=existing_task_map)
            if task is None:
                continue
            if _apply_task_action_update(
                task=task,
                action=action,
                member_map=member_map,
                strict_assignee=False,
            ):
                updated_count += 1
        elif action_name == "delete":
            task = _find_action_task(action=action, existing_task_map=existing_task_map)
            if task is None:
                continue
            task.is_deleted = True
            deleted_count += 1

    await session.commit()

    tasks = await _get_team_tasks(session=session, team_id=team_id)
    return TaskGenerateResponse(
        tasks=[_task_body(task) for task in tasks],
        created_count=created_count,
        updated_count=updated_count,
        deleted_count=deleted_count,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    task = await _get_accessible_task(session=session, auth_user=auth_user, task_id=task_id)
    return TaskResponse(task=_task_body(task))


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    request: TaskUpdateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    task = await _get_accessible_task(session=session, auth_user=auth_user, task_id=task_id)
    members = await _get_active_team_members(session=session, team_id=task.team_id)
    member_map = {user.id: user for user, _role in members}

    if hasattr(request, "model_dump"):
        payload = request.model_dump(exclude_unset=True)
    else:
        payload = request.dict(exclude_unset=True)
    _apply_task_patch(task=task, payload=payload, member_map=member_map)

    await session.commit()
    await session.refresh(task)
    return TaskResponse(task=_task_body(task))


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    task = await _get_accessible_task(session=session, auth_user=auth_user, task_id=task_id)
    task.is_deleted = True
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _get_accessible_task(
    *,
    session: AsyncSession,
    auth_user: AuthenticatedUser,
    task_id: UUID,
) -> Task:
    result = await session.execute(
        select(Task).where(
            Task.id == task_id,
            Task.is_deleted.is_(False),
        )
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")

    await require_team_member(session=session, auth_user=auth_user, team_id=task.team_id)
    return task


async def _get_accessible_minutes(
    *,
    session: AsyncSession,
    team_id: UUID,
    minutes_id: UUID,
) -> MeetingMinutes:
    result = await session.execute(
        select(MeetingMinutes)
        .join(Meeting, MeetingMinutes.meeting_id == Meeting.id)
        .where(
            MeetingMinutes.id == minutes_id,
            MeetingMinutes.is_deleted.is_(False),
            Meeting.team_id == team_id,
            Meeting.is_deleted.is_(False),
        )
    )
    minutes = result.scalar_one_or_none()
    if minutes is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="minutes not found")
    return minutes


async def _get_team_tasks(*, session: AsyncSession, team_id: UUID) -> list[Task]:
    result = await session.execute(
        select(Task)
        .where(Task.team_id == team_id, Task.is_deleted.is_(False))
        .order_by(Task.updated_at.desc(), Task.created_at.desc())
    )
    return list(result.scalars().all())


async def _get_active_team_members(
    *,
    session: AsyncSession,
    team_id: UUID,
) -> list[tuple[User, str]]:
    result = await session.execute(
        select(User, TeamMember.role)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(
            TeamMember.team_id == team_id,
            TeamMember.left_at.is_(None),
            User.is_deleted.is_(False),
        )
        .order_by(User.display_name.asc(), User.email.asc())
    )
    return [(row[0], row[1]) for row in result.all()]


def _create_task_from_action(
    *,
    action: dict,
    team_id: UUID,
    source_minutes_id: UUID,
    member_map: dict[UUID, User],
) -> Task:
    title = _normalize_title(action.get("title"), action.get("body"))
    body = _normalize_body(action.get("body"), title)
    assignee_user_id = _normalize_generated_assignee(action.get("assignee_user_id"), member_map)
    assignee_name = _normalize_assignee_name(
        action.get("assignee_name"),
        assignee_user_id=assignee_user_id,
        member_map=member_map,
    )

    return Task(
        team_id=team_id,
        source_minutes_id=source_minutes_id,
        title=title,
        body=body,
        assignee_user_id=assignee_user_id,
        assignee_name=assignee_name,
        status=_normalize_status(action.get("status")),
        due_at=_parse_generated_due_at(action.get("due_at")),
        is_deleted=False,
    )


def _apply_task_action_update(
    *,
    task: Task,
    action: dict,
    member_map: dict[UUID, User],
    strict_assignee: bool,
) -> bool:
    before = _task_update_snapshot(task)
    if "title" in action:
        task.title = _normalize_title(action.get("title"), task.body)
    if "body" in action:
        task.body = _normalize_body(action.get("body"), task.title)
    if "status" in action:
        task.status = _normalize_status(action.get("status"))
    if "due_at" in action:
        task.due_at = _parse_generated_due_at(action.get("due_at"))
    if "assignee_user_id" in action:
        assignee_user_id = _normalize_generated_assignee(action.get("assignee_user_id"), member_map)
        if strict_assignee and action.get("assignee_user_id") is not None and assignee_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="assignee_user_id must be a team member",
            )
        task.assignee_user_id = assignee_user_id
        task.assignee_name = _normalize_assignee_name(
            action.get("assignee_name"),
            assignee_user_id=assignee_user_id,
            member_map=member_map,
        )
    elif "assignee_name" in action:
        task.assignee_name = _clean_optional_string(action.get("assignee_name"), 255)

    return before != _task_update_snapshot(task)


def _apply_task_patch(
    *,
    task: Task,
    payload: dict,
    member_map: dict[UUID, User],
) -> None:
    if "title" in payload:
        task.title = _normalize_title(payload.get("title"), task.body)
    if "body" in payload:
        task.body = _normalize_body(payload.get("body"), task.title)
    if "status" in payload:
        task.status = _validate_status(payload.get("status"))
    if "due_at" in payload:
        task.due_at = payload["due_at"]
    if "assignee_user_id" in payload:
        assignee_user_id = payload["assignee_user_id"]
        if assignee_user_id is not None and assignee_user_id not in member_map:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="assignee_user_id must be a team member",
            )
        task.assignee_user_id = assignee_user_id
        if assignee_user_id is not None and "assignee_name" not in payload:
            task.assignee_name = member_map[assignee_user_id].display_name
        elif assignee_user_id is None and "assignee_name" not in payload:
            task.assignee_name = None
    if "assignee_name" in payload:
        task.assignee_name = _clean_optional_string(payload.get("assignee_name"), 255)


def _find_action_task(
    *,
    action: dict,
    existing_task_map: dict[UUID, Task],
) -> Task | None:
    task_id = _parse_uuid(action.get("task_id"))
    if task_id is None:
        return None
    return existing_task_map.get(task_id)


def _parse_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def _normalize_generated_assignee(
    value: object,
    member_map: dict[UUID, User],
) -> UUID | None:
    user_id = _parse_uuid(value)
    if user_id is None or user_id not in member_map:
        return None
    return user_id


def _normalize_assignee_name(
    value: object,
    *,
    assignee_user_id: UUID | None,
    member_map: dict[UUID, User],
) -> str | None:
    if assignee_user_id is not None:
        return _clean_optional_string(value, 255) or member_map[assignee_user_id].display_name
    return _clean_optional_string(value, 255)


def _normalize_title(value: object, fallback: object) -> str:
    title = _clean_optional_string(value, 255)
    if title:
        return title

    body = _clean_optional_string(fallback, 255)
    if body:
        return body.splitlines()[0][:255]

    return "Untitled task"


def _normalize_body(value: object, fallback: str) -> str:
    body = str(value or "").strip()
    return body or fallback


def _normalize_status(value: object) -> str:
    status_value = str(value or "").strip().lower()
    return status_value if status_value in VALID_TASK_STATUSES else "todo"


def _validate_status(value: object) -> str:
    status_value = str(value or "").strip().lower()
    if status_value not in VALID_TASK_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid task status",
        )
    return status_value


def _parse_generated_due_at(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    text = str(value).strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clean_optional_string(value: object, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length]


def _task_update_snapshot(task: Task) -> tuple:
    return (
        task.title,
        task.body,
        task.assignee_user_id,
        task.assignee_name,
        task.status,
        task.due_at,
        task.is_deleted,
    )


def _task_body(task: Task) -> TaskBody:
    return TaskBody(
        id=task.id,
        team_id=task.team_id,
        source_minutes_id=task.source_minutes_id,
        title=task.title,
        body=task.body,
        assignee_user_id=task.assignee_user_id,
        assignee_name=task.assignee_name,
        status=task.status,
        due_at=task.due_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _team_member_body(user: User, role: str) -> TeamMemberBody:
    return TeamMemberBody(
        user_id=user.id,
        display_name=user.display_name,
        email=user.email,
        role=role,
    )


def _task_prompt_body(task: Task) -> dict[str, object]:
    return {
        "task_id": str(task.id),
        "title": task.title,
        "body": task.body,
        "assignee_user_id": str(task.assignee_user_id) if task.assignee_user_id else None,
        "assignee_name": task.assignee_name,
        "status": task.status,
        "due_at": task.due_at.isoformat() if task.due_at else None,
    }


def _member_prompt_body(user: User, role: str) -> dict[str, str]:
    return {
        "user_id": str(user.id),
        "display_name": user.display_name,
        "email": user.email,
        "role": role,
    }
