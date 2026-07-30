import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from app.core.auth import AuthenticatedUser, verify_firebase_user
from app.db.session import AsyncSessionLocal, get_db_session
from app.models.meeting import Meeting
from app.models.minutes import MeetingMinutes
from app.models.task import (
    Task,
    TaskGenerationRun,
    TaskMinutesImpact,
    TaskRoadmap,
    TaskRoadmapStep,
)
from app.models.team import TeamMember
from app.models.user import User
from app.services.notion_repository import (
    create_notion_sync_log,
    get_active_notion_connection,
    mark_task_notion_synced,
)
from app.services.notion_service import NotionApiError, sync_task_page
from app.services.tasks_service import (
    TaskGenerationError,
    generate_task_actions_from_minutes,
    get_task_generation_prompt_version,
)
from app.services.task_roadmap_service import (
    TaskRoadmapGenerationError,
    generate_task_roadmap,
    get_task_roadmap_prompt_version,
    task_roadmap_input_hash,
)
from app.services.team_access_service import require_team_member


router = APIRouter()
VALID_TASK_STATUSES = {"todo", "in_progress", "done"}
TASK_STATUS_ORDER = {"todo": 0, "in_progress": 1, "done": 2}
GENERATED_TASK_FIELDS = {
    "action",
    "title",
    "body",
    "assignee_user_id",
    "assignee_name",
    "status",
    "due_at",
}
ROADMAP_GENERATION_STALE_AFTER = timedelta(minutes=2)
ROADMAP_GENERATION_CONCURRENCY = 2
roadmap_generation_semaphore = asyncio.Semaphore(ROADMAP_GENERATION_CONCURRENCY)


@dataclass(frozen=True)
class RoadmapGenerationInput:
    generation_token: UUID
    task: dict[str, object]
    related_minutes: list[dict[str, object]]
    input_hash: str
    prompt_version: str


class RoadmapStepBody(BaseModel):
    id: UUID
    title: str
    description: str
    status: str
    position: int
    source: str
    user_edited: bool


class TaskRoadmapBody(BaseModel):
    id: UUID
    overview: str
    generation_status: str
    generation_error: str | None
    generation_started_at: datetime | None
    version: int
    has_source_updates: bool
    steps: list[RoadmapStepBody]


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
    roadmap: TaskRoadmapBody | None = None


class TaskListResponse(BaseModel):
    tasks: list[TaskBody]


class TaskResponse(BaseModel):
    task: TaskBody


class TaskGenerateRequest(BaseModel):
    minutes_id: UUID


class TaskCreateRequest(BaseModel):
    title: str
    body: str | None = None
    assignee_user_id: UUID | None = None
    status: str = "todo"
    due_at: datetime | None = None


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


class RoadmapGenerateRequest(BaseModel):
    reopen: bool = False
    expected_version: int | None = None


class RoadmapStepCreateRequest(BaseModel):
    title: str
    description: str
    expected_version: int | None = None


class RoadmapStepUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    reopen_task: bool = False
    expected_version: int | None = None


class RoadmapStepReorderRequest(BaseModel):
    step_ids: list[UUID]
    expected_version: int | None = None


class NotionSyncBody(BaseModel):
    status: str
    task_id: UUID
    notion_page_id: str
    synced_at: datetime


class NotionSyncResponse(BaseModel):
    notion_sync: NotionSyncBody


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


@router.get(
    "/teams/{team_id}/minutes/{minutes_id}/tasks",
    response_model=TaskListResponse,
)
async def list_minutes_tasks(
    team_id: UUID,
    minutes_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskListResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)
    await _get_accessible_minutes(
        session=session,
        team_id=team_id,
        minutes_id=minutes_id,
    )

    statement = (
        select(Task)
        .outerjoin(TaskMinutesImpact, TaskMinutesImpact.task_id == Task.id)
        .where(
            Task.team_id == team_id,
            Task.is_deleted.is_(False),
            or_(
                Task.source_minutes_id == minutes_id,
                TaskMinutesImpact.minutes_id == minutes_id,
            ),
        )
        .distinct()
        .order_by(Task.updated_at.desc(), Task.created_at.desc())
    )
    result = await session.execute(statement)
    return TaskListResponse(tasks=[_task_body(task) for task in result.scalars().all()])


async def create_team_task(
    team_id: UUID,
    request: TaskCreateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)

    title = _clean_optional_string(request.title, 255)
    if title is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="task title is required",
        )

    members = await _get_active_team_members(session=session, team_id=team_id)
    member_map = {user.id: user for user, _role in members}
    if request.assignee_user_id is not None and request.assignee_user_id not in member_map:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="assignee_user_id must be a team member",
        )

    assignee = member_map.get(request.assignee_user_id)
    task = Task(
        team_id=team_id,
        source_minutes_id=None,
        title=title,
        body=_normalize_body(request.body, title),
        assignee_user_id=request.assignee_user_id,
        assignee_name=assignee.display_name if assignee is not None else None,
        status=_validate_status(request.status),
        due_at=request.due_at,
        is_deleted=False,
    )
    task.roadmap = TaskRoadmap(
        overview="",
        generation_status="pending",
        version=1,
        has_source_updates=False,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return TaskResponse(task=_task_body(task))


@router.post(
    "/teams/{team_id}/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_team_task_endpoint(
    team_id: UUID,
    request: TaskCreateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    return await create_team_task(
        team_id=team_id,
        request=request,
        auth_user=auth_user,
        session=session,
    )


async def generate_team_tasks(
    team_id: UUID,
    request: TaskGenerateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskGenerateResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)

    minutes, meeting = await _get_accessible_minutes(
        session=session,
        team_id=team_id,
        minutes_id=request.minutes_id,
    )
    conversation_logs = _conversation_logs_from_meeting(meeting)
    input_hash = _task_generation_input_hash(
        conversation_logs=conversation_logs,
        minutes_body=minutes.body,
    )
    prompt_version = get_task_generation_prompt_version()
    previous_run = await _get_successful_task_generation_run(
        session=session,
        team_id=team_id,
        minutes_id=minutes.id,
        input_hash=input_hash,
        prompt_version=prompt_version,
    )
    if previous_run is not None:
        tasks = await _get_team_tasks(session=session, team_id=team_id)
        return TaskGenerateResponse(
            tasks=[_task_body(task) for task in tasks],
            created_count=0,
            updated_count=0,
            deleted_count=0,
        )

    members = await _get_active_team_members(session=session, team_id=team_id)
    member_map = {user.id: user for user, _role in members}
    existing_tasks = await _get_team_tasks(session=session, team_id=team_id)

    try:
        actions = await run_in_threadpool(
            generate_task_actions_from_minutes,
            conversation_logs=conversation_logs,
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

    for raw_action in actions:
        action = _validate_generated_task_action(
            action=raw_action,
            existing_task_map=existing_task_map,
            member_map=member_map,
        )
        if action is None:
            continue

        action_name = str(action.get("action") or "").strip().lower()
        if action_name == "create":
            task = _create_task_from_action(
                action=action,
                team_id=team_id,
                source_minutes_id=minutes.id,
                member_map=member_map,
            )
            session.add(task)
            session.add(
                TaskMinutesImpact(
                    task=task,
                    minutes_id=minutes.id,
                    action="created",
                )
            )
            created_count += 1
        elif action_name == "update":
            task = _find_action_task(action=action, existing_task_map=existing_task_map)
            if task is None or task.status == "done":
                continue
            if _apply_task_action_update(
                task=task,
                action=action,
                member_map=member_map,
                strict_assignee=True,
            ):
                _mark_roadmap_pending(_ensure_roadmap(task))
                session.add(
                    TaskMinutesImpact(
                        task_id=task.id,
                        minutes_id=minutes.id,
                        action="updated",
                    )
                )
                updated_count += 1

    session.add(
        TaskGenerationRun(
            team_id=team_id,
            minutes_id=minutes.id,
            input_hash=input_hash,
            prompt_version=prompt_version,
            created_count=created_count,
            updated_count=updated_count,
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        concurrent_run = await _get_successful_task_generation_run(
            session=session,
            team_id=team_id,
            minutes_id=minutes.id,
            input_hash=input_hash,
            prompt_version=prompt_version,
        )
        if concurrent_run is None:
            raise
        created_count = 0
        updated_count = 0

    tasks = await _get_team_tasks(session=session, team_id=team_id)
    return TaskGenerateResponse(
        tasks=[_task_body(task) for task in tasks],
        created_count=created_count,
        updated_count=updated_count,
        deleted_count=deleted_count,
    )


@router.post("/teams/{team_id}/tasks/generate", response_model=TaskGenerateResponse)
async def generate_team_tasks_endpoint(
    team_id: UUID,
    request: TaskGenerateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskGenerateResponse:
    return await generate_team_tasks(
        team_id=team_id,
        request=request,
        auth_user=auth_user,
        session=session,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    task = await _get_accessible_task(session=session, auth_user=auth_user, task_id=task_id)
    return TaskResponse(task=_task_body(task))


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
    previous_status = task.status
    source_changed = any(field in payload for field in ("title", "body"))
    _apply_task_patch(task=task, payload=payload, member_map=member_map)

    roadmap = _ensure_roadmap(task)
    if task.status == "done":
        _complete_all_roadmap_steps(roadmap)
        if source_changed and previous_status == "done":
            roadmap.has_source_updates = True
    elif previous_status == "done" and _active_roadmap_steps(roadmap):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="reopen the task through its roadmap",
        )
    elif source_changed:
        _mark_roadmap_pending(roadmap)

    await session.commit()
    await session.refresh(task)
    return TaskResponse(task=_task_body(task))


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task_endpoint(
    task_id: UUID,
    request: TaskUpdateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    return await update_task(
        task_id=task_id,
        request=request,
        auth_user=auth_user,
        session=session,
    )


@router.post("/tasks/{task_id}/roadmap/generate", response_model=TaskResponse)
async def generate_task_roadmap_endpoint(
    task_id: UUID,
    request: RoadmapGenerateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    task = await _get_accessible_task(session=session, auth_user=auth_user, task_id=task_id)
    roadmap = _ensure_roadmap(task)
    _check_roadmap_version(roadmap, request.expected_version)
    if (
        roadmap.generation_status == "generating"
        and not _roadmap_generation_is_stale(roadmap)
    ):
        return TaskResponse(task=_task_body(task))
    is_initial_generation = not _active_roadmap_steps(roadmap) and not roadmap.overview
    if task.status == "done" and not request.reopen and not is_initial_generation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="completed task must be reopened before roadmap generation",
        )
    if request.reopen:
        task.status = "in_progress"

    generation_token = _mark_roadmap_pending(roadmap)
    roadmap.has_source_updates = False
    await session.commit()
    await generate_task_roadmap_background(task.id, generation_token)
    session.expire_all()
    refreshed_task = await _get_task_for_roadmap_generation(
        session=session,
        task_id=task.id,
    )
    if refreshed_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="task not found",
        )
    return TaskResponse(task=_task_body(refreshed_task))


@router.post(
    "/tasks/{task_id}/roadmap/steps",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_roadmap_step(
    task_id: UUID,
    request: RoadmapStepCreateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    task = await _get_accessible_task(session=session, auth_user=auth_user, task_id=task_id)
    roadmap = _ensure_roadmap(task)
    _check_roadmap_version(roadmap, request.expected_version)
    title = _required_roadmap_text(request.title, "step title", 255)
    description = _required_roadmap_text(request.description, "step description", 5000)
    next_position = max((step.position for step in roadmap.steps), default=-1) + 1
    roadmap.steps.append(
        TaskRoadmapStep(
            title=title,
            description=description,
            status="todo",
            position=next_position,
            source="user",
            user_edited=True,
            is_deleted=False,
        )
    )
    roadmap.version += 1
    roadmap.has_source_updates = False
    if task.status == "done":
        task.status = "in_progress"
    await session.commit()
    await session.refresh(task)
    return TaskResponse(task=_task_body(task))


@router.patch(
    "/tasks/{task_id}/roadmap/steps/{step_id}",
    response_model=TaskResponse,
)
async def update_roadmap_step(
    task_id: UUID,
    step_id: UUID,
    request: RoadmapStepUpdateRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    task = await _get_accessible_task(session=session, auth_user=auth_user, task_id=task_id)
    roadmap = _ensure_roadmap(task)
    _check_roadmap_version(roadmap, request.expected_version)
    step = _find_roadmap_step(roadmap, step_id)

    if hasattr(request, "model_dump"):
        payload = request.model_dump(exclude_unset=True)
    else:
        payload = request.dict(exclude_unset=True)

    content_changed = False
    if "title" in payload:
        step.title = _required_roadmap_text(payload["title"], "step title", 255)
        content_changed = True
    if "description" in payload:
        step.description = _required_roadmap_text(
            payload["description"],
            "step description",
            5000,
        )
        content_changed = True
    if "status" in payload:
        next_status = _validate_status(payload["status"])
        if task.status == "done" and next_status != "done":
            if not request.reopen_task:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="reopen_task is required",
                )
            task.status = "in_progress"
        step.status = next_status
    if content_changed:
        step.user_edited = True

    roadmap.version += 1
    _sync_task_completion_from_steps(task, roadmap)
    await session.commit()
    await session.refresh(task)
    return TaskResponse(task=_task_body(task))


@router.delete(
    "/tasks/{task_id}/roadmap/steps/{step_id}",
    response_model=TaskResponse,
)
async def delete_roadmap_step(
    task_id: UUID,
    step_id: UUID,
    expected_version: int | None = None,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    task = await _get_accessible_task(session=session, auth_user=auth_user, task_id=task_id)
    roadmap = _ensure_roadmap(task)
    _check_roadmap_version(roadmap, expected_version)
    step = _find_roadmap_step(roadmap, step_id)
    step.is_deleted = True
    step.user_edited = True
    roadmap.version += 1
    _sync_task_completion_from_steps(task, roadmap)
    await session.commit()
    await session.refresh(task)
    return TaskResponse(task=_task_body(task))


@router.put("/tasks/{task_id}/roadmap/reorder", response_model=TaskResponse)
async def reorder_roadmap_steps(
    task_id: UUID,
    request: RoadmapStepReorderRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    task = await _get_accessible_task(session=session, auth_user=auth_user, task_id=task_id)
    roadmap = _ensure_roadmap(task)
    _check_roadmap_version(roadmap, request.expected_version)
    active_steps = _active_roadmap_steps(roadmap)
    active_map = {step.id: step for step in active_steps}
    if len(request.step_ids) != len(set(request.step_ids)) or set(request.step_ids) != set(active_map):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="step_ids must contain every active roadmap step exactly once",
        )

    for index, step in enumerate(roadmap.steps):
        step.position = -(index + 1)
    await session.flush()
    for position, step_id in enumerate(request.step_ids):
        active_map[step_id].position = position
    deleted_steps = [step for step in roadmap.steps if step.is_deleted]
    for offset, step in enumerate(deleted_steps, start=len(active_steps)):
        step.position = offset
    roadmap.version += 1
    await session.commit()
    await session.refresh(task)
    return TaskResponse(task=_task_body(task))


@router.post("/tasks/{task_id}/notion-sync", response_model=NotionSyncResponse)
async def sync_task_to_notion(
    task_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> NotionSyncResponse:
    task = await _get_accessible_task(
        session=session,
        auth_user=auth_user,
        task_id=task_id,
    )
    connection = await get_active_notion_connection(
        session=session,
        team_id=task.team_id,
    )
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notion connection not found",
        )

    database_id = (connection.default_database_id or "").strip()
    if not database_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="notion default database is not configured",
        )

    try:
        notion_page = await sync_task_page(
            access_token=connection.access_token,
            database_id=database_id,
            task_id=str(task.id),
            title=task.title,
            body=task.body,
            status=task.status,
            assignee_name=task.assignee_name,
            due_at=task.due_at,
            notion_page_id=task.notion_page_id,
        )
    except NotionApiError as exc:
        await create_notion_sync_log(
            session=session,
            task=task,
            connection=connection,
            notion_page_id=task.notion_page_id,
            status="failed",
            error_message=str(exc),
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to sync task to notion",
        ) from exc

    synced_at = datetime.now(timezone.utc)
    await mark_task_notion_synced(
        session=session,
        task=task,
        notion_page_id=notion_page.id,
        synced_at=synced_at,
    )
    await create_notion_sync_log(
        session=session,
        task=task,
        connection=connection,
        notion_page_id=notion_page.id,
        status="success",
    )
    await session.commit()

    return NotionSyncResponse(
        notion_sync=NotionSyncBody(
            status="success",
            task_id=task.id,
            notion_page_id=notion_page.id,
            synced_at=synced_at,
        )
    )


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


async def generate_task_roadmap_background(
    task_id: UUID,
    generation_token: UUID | None = None,
) -> None:
    generation_input = await _prepare_task_roadmap_generation(
        task_id=task_id,
        generation_token=generation_token,
    )
    if generation_input is None:
        return

    try:
        async with roadmap_generation_semaphore:
            generated = await run_in_threadpool(
                generate_task_roadmap,
                task=generation_input.task,
                related_minutes=generation_input.related_minutes,
            )
    except Exception as exc:
        await _mark_task_roadmap_generation_failed(
            task_id=task_id,
            generation_token=generation_input.generation_token,
            error=exc,
        )
        return

    try:
        await _apply_task_roadmap_generation(
            task_id=task_id,
            generation_token=generation_input.generation_token,
            generation_input=generation_input,
            generated=generated,
        )
    except Exception as exc:
        await _mark_task_roadmap_generation_failed(
            task_id=task_id,
            generation_token=generation_input.generation_token,
            error=exc,
        )


async def _prepare_task_roadmap_generation(
    *,
    task_id: UUID,
    generation_token: UUID | None,
) -> RoadmapGenerationInput | None:
    async with AsyncSessionLocal() as session:
        task = await _get_task_for_roadmap_generation(session=session, task_id=task_id)
        if task is None:
            return
        roadmap = _ensure_roadmap(task)
        if generation_token is not None and roadmap.generation_token != generation_token:
            return
        if generation_token is None:
            if (
                roadmap.generation_status == "generating"
                and not _roadmap_generation_is_stale(roadmap)
            ):
                return
            generation_token = _mark_roadmap_pending(roadmap)

        roadmap.generation_status = "generating"
        roadmap.generation_error = None
        roadmap.generation_started_at = datetime.now(timezone.utc)
        await session.commit()

        task_payload = _roadmap_task_prompt_body(task)
        related_minutes = await _get_task_related_minutes(session=session, task=task)
        minutes_payload = [
            {
                "minutes_id": str(minutes.id),
                "title": minutes.title,
                "body": minutes.body,
                "updated_at": minutes.updated_at.isoformat(),
            }
            for minutes in related_minutes
        ]
        input_hash = task_roadmap_input_hash(
            task=task_payload,
            related_minutes=minutes_payload,
        )
        prompt_version = get_task_roadmap_prompt_version()

        if roadmap.input_hash == input_hash and roadmap.prompt_version == prompt_version:
            roadmap.generation_status = "ready"
            roadmap.generation_token = None
            roadmap.generation_started_at = None
            roadmap.has_source_updates = False
            await session.commit()
            return

        generation_input = RoadmapGenerationInput(
            generation_token=generation_token,
            task=task_payload,
            related_minutes=minutes_payload,
            input_hash=input_hash,
            prompt_version=prompt_version,
        )
        # The SELECT above starts a transaction. End it before waiting for Gemini so
        # unrelated API requests can continue using the database connection pool.
        await session.rollback()
        return generation_input


async def _mark_task_roadmap_generation_failed(
    *,
    task_id: UUID,
    generation_token: UUID | None,
    error: Exception,
) -> None:
    async with AsyncSessionLocal() as session:
        task = await _get_task_for_roadmap_generation(session=session, task_id=task_id)
        if task is None:
            return
        roadmap = _ensure_roadmap(task)
        if roadmap.generation_token != generation_token:
            return
        roadmap.generation_status = "failed"
        roadmap.generation_error = (
            "AIによるロードマップ生成に失敗しました。再試行してください。"
            if isinstance(error, TaskRoadmapGenerationError)
            else "ロードマップ生成中に予期しないエラーが発生しました。"
        )
        roadmap.generation_token = None
        roadmap.generation_started_at = None
        await session.commit()


async def _apply_task_roadmap_generation(
    *,
    task_id: UUID,
    generation_token: UUID | None,
    generation_input: RoadmapGenerationInput,
    generated: dict,
) -> None:
    async with AsyncSessionLocal() as session:
        task = await _get_task_for_roadmap_generation(session=session, task_id=task_id)
        if task is None:
            return
        roadmap = _ensure_roadmap(task)
        await session.refresh(roadmap, attribute_names=["steps"])
        if roadmap.generation_token != generation_token:
            return
        await session.refresh(task)
        _merge_generated_roadmap(task=task, roadmap=roadmap, generated=generated)
        roadmap.input_hash = generation_input.input_hash
        roadmap.prompt_version = generation_input.prompt_version
        roadmap.generation_status = "ready"
        roadmap.generation_error = None
        roadmap.generation_token = None
        roadmap.generation_started_at = None
        roadmap.has_source_updates = False
        roadmap.last_generated_at = datetime.now(timezone.utc)
        roadmap.version += 1
        _sync_task_completion_from_steps(task, roadmap)
        await session.commit()


async def _get_task_for_roadmap_generation(
    *,
    session: AsyncSession,
    task_id: UUID,
) -> Task | None:
    result = await session.execute(
        select(Task)
        .options(selectinload(Task.roadmap).selectinload(TaskRoadmap.steps))
        .where(Task.id == task_id, Task.is_deleted.is_(False))
    )
    return result.scalar_one_or_none()


async def _get_task_related_minutes(
    *,
    session: AsyncSession,
    task: Task,
) -> list[MeetingMinutes]:
    statement = (
        select(MeetingMinutes)
        .outerjoin(
            TaskMinutesImpact,
            TaskMinutesImpact.minutes_id == MeetingMinutes.id,
        )
        .where(
            MeetingMinutes.is_deleted.is_(False),
            or_(
                MeetingMinutes.id == task.source_minutes_id,
                TaskMinutesImpact.task_id == task.id,
            ),
        )
        .distinct()
        .order_by(MeetingMinutes.updated_at.asc())
    )
    result = await session.execute(statement)
    return list(result.scalars().all())


async def mark_minutes_roadmaps_for_regeneration(
    *,
    session: AsyncSession,
    minutes_id: UUID,
) -> list[tuple[UUID, UUID]]:
    result = await session.execute(
        select(Task)
        .options(selectinload(Task.roadmap).selectinload(TaskRoadmap.steps))
        .outerjoin(TaskMinutesImpact, TaskMinutesImpact.task_id == Task.id)
        .where(
            Task.is_deleted.is_(False),
            or_(
                Task.source_minutes_id == minutes_id,
                TaskMinutesImpact.minutes_id == minutes_id,
            ),
        )
        .distinct()
    )
    jobs: list[tuple[UUID, UUID]] = []
    for task in result.scalars().all():
        roadmap = _ensure_roadmap(task)
        if task.status == "done":
            roadmap.has_source_updates = True
            continue
        token = _mark_roadmap_pending(roadmap)
        jobs.append((task.id, token))
    return jobs


def _merge_generated_roadmap(
    *,
    task: Task,
    roadmap: TaskRoadmap,
    generated: dict,
) -> None:
    roadmap.overview = str(generated["overview"]).strip()
    all_step_map = {step.id: step for step in roadmap.steps}
    active_steps = _active_roadmap_steps(roadmap)
    active_title_map = {_normalized_step_title(step.title): step for step in active_steps}
    deleted_titles = {
        _normalized_step_title(step.title) for step in roadmap.steps if step.is_deleted
    }
    matched_ids: set[UUID] = set()
    next_position = max((step.position for step in roadmap.steps), default=-1) + 1

    for generated_step in generated["steps"]:
        step = all_step_map.get(generated_step.get("existing_step_id"))
        normalized_title = _normalized_step_title(generated_step["title"])
        if step is None:
            step = active_title_map.get(normalized_title)
        if step is not None:
            if step.is_deleted:
                continue
            matched_ids.add(step.id)
            if step.source == "ai" and not step.user_edited and step.status == "todo":
                step.title = generated_step["title"]
                step.description = generated_step["description"]
            continue
        if normalized_title in deleted_titles:
            continue

        step = TaskRoadmapStep(
            title=generated_step["title"],
            description=generated_step["description"],
            status="done" if task.status == "done" else "todo",
            position=next_position,
            source="ai",
            user_edited=False,
            is_deleted=False,
        )
        roadmap.steps.append(step)
        next_position += 1

    for step in active_steps:
        if (
            step.id not in matched_ids
            and step.source == "ai"
            and not step.user_edited
            and step.status == "todo"
        ):
            step.is_deleted = True


def _roadmap_task_prompt_body(task: Task) -> dict[str, object]:
    return {
        "task_id": str(task.id),
        "title": task.title,
        "body": task.body,
        "status": task.status,
        "assignee_name": task.assignee_name,
        "due_at": task.due_at.isoformat() if task.due_at else None,
    }


def _ensure_roadmap(task: Task) -> TaskRoadmap:
    if task.roadmap is None:
        task.roadmap = TaskRoadmap(
            overview="",
            generation_status="pending",
            version=1,
            has_source_updates=False,
        )
    return task.roadmap


def _mark_roadmap_pending(roadmap: TaskRoadmap) -> UUID:
    generation_token = uuid4()
    roadmap.generation_status = "pending"
    roadmap.generation_error = None
    roadmap.generation_token = generation_token
    roadmap.generation_started_at = None
    roadmap.version += 1
    return generation_token


def _roadmap_generation_is_stale(
    roadmap: TaskRoadmap,
    *,
    now: datetime | None = None,
) -> bool:
    if roadmap.generation_status != "generating":
        return False
    if roadmap.generation_started_at is None:
        return True
    started_at = roadmap.generation_started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    return current_time - started_at >= ROADMAP_GENERATION_STALE_AFTER


def _active_roadmap_steps(roadmap: TaskRoadmap) -> list[TaskRoadmapStep]:
    return sorted(
        (step for step in roadmap.steps if not step.is_deleted),
        key=lambda step: step.position,
    )


def _complete_all_roadmap_steps(roadmap: TaskRoadmap) -> None:
    for step in _active_roadmap_steps(roadmap):
        step.status = "done"


def _sync_task_completion_from_steps(task: Task, roadmap: TaskRoadmap) -> None:
    steps = _active_roadmap_steps(roadmap)
    if steps and all(step.status == "done" for step in steps):
        task.status = "done"


def _find_roadmap_step(roadmap: TaskRoadmap, step_id: UUID) -> TaskRoadmapStep:
    for step in roadmap.steps:
        if step.id == step_id and not step.is_deleted:
            return step
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="roadmap step not found",
    )


def _required_roadmap_text(value: object, field_name: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} is required",
        )
    return text[:max_length]


def _normalized_step_title(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _check_roadmap_version(
    roadmap: TaskRoadmap,
    expected_version: int | None,
) -> None:
    if expected_version is not None and expected_version != roadmap.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="roadmap was updated by another user",
        )


async def _get_accessible_task(
    *,
    session: AsyncSession,
    auth_user: AuthenticatedUser,
    task_id: UUID,
) -> Task:
    result = await session.execute(
        select(Task)
        .options(selectinload(Task.roadmap).selectinload(TaskRoadmap.steps))
        .where(
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
) -> tuple[MeetingMinutes, Meeting]:
    result = await session.execute(
        select(MeetingMinutes, Meeting)
        .join(Meeting, MeetingMinutes.meeting_id == Meeting.id)
        .where(
            MeetingMinutes.id == minutes_id,
            MeetingMinutes.is_deleted.is_(False),
            Meeting.team_id == team_id,
            Meeting.is_deleted.is_(False),
        )
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="minutes not found")
    return row[0], row[1]


def _task_generation_input_hash(*, conversation_logs: str, minutes_body: str) -> str:
    payload = json.dumps(
        {
            "conversation_logs": conversation_logs,
            "minutes_body": minutes_body,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _get_successful_task_generation_run(
    *,
    session: AsyncSession,
    team_id: UUID,
    minutes_id: UUID,
    input_hash: str,
    prompt_version: str,
) -> TaskGenerationRun | None:
    result = await session.execute(
        select(TaskGenerationRun).where(
            TaskGenerationRun.team_id == team_id,
            TaskGenerationRun.minutes_id == minutes_id,
            TaskGenerationRun.input_hash == input_hash,
            TaskGenerationRun.prompt_version == prompt_version,
        )
    )
    return result.scalar_one_or_none()


def _validate_generated_task_action(
    *,
    action: object,
    existing_task_map: dict[UUID, Task],
    member_map: dict[UUID, User],
) -> dict | None:
    if not isinstance(action, dict):
        return None

    action_name = str(action.get("action") or "").strip().lower()
    if action_name == "create":
        allowed_fields = GENERATED_TASK_FIELDS
        required_fields = {"title", "body", "status"}
        task = None
    elif action_name == "update":
        allowed_fields = GENERATED_TASK_FIELDS | {"task_id"}
        required_fields = {"task_id"}
        task = _find_action_task(action=action, existing_task_map=existing_task_map)
        if task is None or task.status == "done":
            return None
    else:
        return None

    if set(action) - allowed_fields or not required_fields.issubset(action):
        return None

    update_fields = GENERATED_TASK_FIELDS - {"action"}
    if action_name == "update" and not any(field in action for field in update_fields):
        return None

    validated: dict[str, object] = {"action": action_name}
    if task is not None:
        validated["task_id"] = task.id

    for field in ("title", "body"):
        if field not in action:
            continue
        value = action[field]
        if not isinstance(value, str) or not value.strip():
            return None
        if field == "title" and len(value.strip()) > 255:
            return None
        validated[field] = value.strip()

    if "status" in action:
        status_value = action["status"]
        if not isinstance(status_value, str) or status_value not in VALID_TASK_STATUSES:
            return None
        if (
            task is not None
            and TASK_STATUS_ORDER[status_value] < TASK_STATUS_ORDER[task.status]
        ):
            return None
        validated["status"] = status_value

    if "due_at" in action:
        due_at_is_valid, due_at = _parse_strict_generated_due_at(action["due_at"])
        if not due_at_is_valid:
            return None
        validated["due_at"] = due_at

    if "assignee_name" in action and action["assignee_name"] is not None:
        assignee_name = action["assignee_name"]
        if not isinstance(assignee_name, str) or not assignee_name.strip():
            return None
        if len(assignee_name.strip()) > 255:
            return None

    if "assignee_user_id" in action:
        assignee_value = action["assignee_user_id"]
        if assignee_value is None:
            if action.get("assignee_name") is not None:
                return None
            validated["assignee_user_id"] = None
            validated["assignee_name"] = None
        else:
            assignee_user_id = _parse_uuid(assignee_value)
            if assignee_user_id is None or assignee_user_id not in member_map:
                return None
            validated["assignee_user_id"] = assignee_user_id
            validated["assignee_name"] = member_map[assignee_user_id].display_name
    elif "assignee_name" in action:
        return None

    return validated


def _parse_strict_generated_due_at(value: object) -> tuple[bool, datetime | None]:
    if value is None:
        return True, None
    if not isinstance(value, str) or not value.strip():
        return False, None
    parsed = _parse_generated_due_at(value)
    return parsed is not None, parsed



def _conversation_logs_from_meeting(meeting: Meeting) -> str:
    payload = meeting.aiboard_payload if isinstance(meeting.aiboard_payload, dict) else {}
    sections: list[str] = []

    themes = payload.get("themes") if isinstance(payload, dict) else None
    if isinstance(themes, list):
        for theme in themes:
            if not isinstance(theme, dict):
                continue
            title = _clean_optional_string(theme.get("title"), 255)
            lines = _logs_to_lines(theme.get("logs"))
            if not lines:
                continue
            if title:
                sections.append(f"## {title}")
            sections.extend(lines)

    current_logs = payload.get("current_logs") if isinstance(payload, dict) else None
    current_lines = _logs_to_lines(current_logs)
    if current_lines:
        sections.append("## current_logs")
        sections.extend(current_lines)

    return "\n".join(sections).strip()


def _logs_to_lines(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    lines: list[str] = []
    for log in value:
        content = _log_content(log)
        if content:
            lines.append(content)
    return lines


def _log_content(log: object) -> str | None:
    if isinstance(log, str):
        return _clean_optional_string(log, 5000)
    if not isinstance(log, dict):
        return None

    for key in ("content", "text", "message", "body"):
        content = _clean_optional_string(log.get(key), 5000)
        if content:
            return content
    return None

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

    task = Task(
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
    task.roadmap = TaskRoadmap(
        overview="",
        generation_status="pending",
        version=1,
        has_source_updates=False,
    )
    return task


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
    status_value = _normalize_status_value(value)
    return status_value if status_value in VALID_TASK_STATUSES else "todo"


def _validate_status(value: object) -> str:
    status_value = _normalize_status_value(value)
    if status_value not in VALID_TASK_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid task status",
        )
    return status_value


def _normalize_status_value(value: object) -> str:
    status_value = str(value or "").strip().lower()
    return "in_progress" if status_value == "doing" else status_value


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
        roadmap=_roadmap_body(task.roadmap),
    )


def _roadmap_body(roadmap: TaskRoadmap | None) -> TaskRoadmapBody | None:
    if roadmap is None or not isinstance(roadmap.id, UUID):
        return None
    return TaskRoadmapBody(
        id=roadmap.id,
        overview=roadmap.overview,
        generation_status=roadmap.generation_status,
        generation_error=roadmap.generation_error,
        generation_started_at=roadmap.generation_started_at,
        version=roadmap.version,
        has_source_updates=roadmap.has_source_updates,
        steps=[
            RoadmapStepBody(
                id=step.id,
                title=step.title,
                description=step.description,
                status=step.status,
                position=step.position,
                source=step.source,
                user_edited=step.user_edited,
            )
            for step in _active_roadmap_steps(roadmap)
            if isinstance(step.id, UUID)
        ],
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
