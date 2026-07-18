from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notion import (
    AiboardNotionConnection,
    AiboardNotionOAuthState,
    AiboardNotionSyncLog,
    AiboardTask,
    NotionConnection,
    NotionOAuthState,
    NotionSyncLog,
)
from app.models.task import Task

ACTIVE_CONNECTION_STATUS = "active"
REVOKED_CONNECTION_STATUS = "revoked"
SUCCESS_SYNC_STATUS = "success"
FAILED_SYNC_STATUS = "failed"
VALID_TASK_STATUSES = {"todo", "in_progress", "done"}


async def get_active_notion_connection(
    *,
    session: AsyncSession,
    team_id: UUID,
) -> NotionConnection | None:
    result = await session.execute(
        select(NotionConnection)
        .where(
            NotionConnection.team_id == team_id,
            NotionConnection.status == ACTIVE_CONNECTION_STATUS,
            NotionConnection.is_deleted.is_(False),
        )
        .order_by(NotionConnection.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def upsert_notion_connection(
    *,
    session: AsyncSession,
    team_id: UUID,
    notion_workspace_id: str,
    access_token: str,
    notion_workspace_name: str | None = None,
    bot_id: str | None = None,
    default_database_id: str | None = None,
    default_database_name: str | None = None,
) -> NotionConnection:
    connection = await get_active_notion_connection(session=session, team_id=team_id)
    if connection is None:
        connection = NotionConnection(
            team_id=team_id,
            notion_workspace_id=notion_workspace_id,
            notion_workspace_name=notion_workspace_name,
            bot_id=bot_id,
            access_token=access_token,
            default_database_id=default_database_id,
            default_database_name=default_database_name,
            status=ACTIVE_CONNECTION_STATUS,
            is_deleted=False,
        )
        session.add(connection)
    else:
        connection.notion_workspace_id = notion_workspace_id
        connection.notion_workspace_name = notion_workspace_name
        connection.bot_id = bot_id
        connection.access_token = access_token
        if default_database_id is not None:
            connection.default_database_id = default_database_id
        if default_database_name is not None:
            connection.default_database_name = default_database_name
        connection.status = ACTIVE_CONNECTION_STATUS
        connection.is_deleted = False

    await session.flush()
    await session.refresh(connection)
    return connection


async def set_notion_default_database(
    *,
    session: AsyncSession,
    connection: NotionConnection,
    database_id: str | None,
    database_name: str | None,
) -> NotionConnection:
    connection.default_database_id = _clean_optional_string(database_id, 255)
    connection.default_database_name = _clean_optional_string(database_name, 255)
    await session.flush()
    await session.refresh(connection)
    return connection


async def revoke_notion_connection(
    *,
    session: AsyncSession,
    connection: NotionConnection,
) -> NotionConnection:
    connection.status = REVOKED_CONNECTION_STATUS
    connection.is_deleted = True
    await session.flush()
    await session.refresh(connection)
    return connection


async def create_notion_oauth_state(
    *,
    session: AsyncSession,
    team_id: UUID,
    state: str,
    expires_at: datetime,
) -> NotionOAuthState:
    oauth_state = NotionOAuthState(
        team_id=team_id,
        state=state,
        expires_at=expires_at,
        consumed_at=None,
    )
    session.add(oauth_state)
    await session.flush()
    await session.refresh(oauth_state)
    return oauth_state


async def get_notion_oauth_state(
    *,
    session: AsyncSession,
    state: str,
) -> NotionOAuthState | None:
    result = await session.execute(
        select(NotionOAuthState).where(NotionOAuthState.state == state).limit(1)
    )
    return result.scalar_one_or_none()


async def consume_notion_oauth_state(
    *,
    session: AsyncSession,
    oauth_state: NotionOAuthState,
) -> NotionOAuthState:
    oauth_state.consumed_at = datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(oauth_state)
    return oauth_state


async def create_notion_sync_log(
    *,
    session: AsyncSession,
    task: Task,
    connection: NotionConnection,
    status: str,
    notion_page_id: str | None = None,
    error_message: str | None = None,
) -> NotionSyncLog:
    sync_log = NotionSyncLog(
        task_id=task.id,
        notion_connection_id=connection.id,
        notion_page_id=_clean_optional_string(notion_page_id, 255),
        status=_validate_sync_status(status),
        error_message=_clean_optional_string(error_message, 1000),
        is_deleted=False,
    )
    session.add(sync_log)
    await session.flush()
    await session.refresh(sync_log)
    return sync_log


async def mark_task_notion_synced(
    *,
    session: AsyncSession,
    task: Task,
    notion_page_id: str,
    synced_at: datetime | None = None,
) -> Task:
    task.notion_page_id = _clean_optional_string(notion_page_id, 255)
    task.notion_last_synced_at = synced_at or datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(task)
    return task


async def get_active_aiboard_notion_connection(
    *,
    session: AsyncSession,
    aiboard_meeting_id: UUID,
) -> AiboardNotionConnection | None:
    result = await session.execute(
        select(AiboardNotionConnection)
        .where(
            AiboardNotionConnection.aiboard_meeting_id == aiboard_meeting_id,
            AiboardNotionConnection.status == ACTIVE_CONNECTION_STATUS,
            AiboardNotionConnection.is_deleted.is_(False),
        )
        .order_by(AiboardNotionConnection.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def upsert_aiboard_notion_connection(
    *,
    session: AsyncSession,
    aiboard_meeting_id: UUID,
    notion_workspace_id: str,
    access_token: str,
    notion_workspace_name: str | None = None,
    bot_id: str | None = None,
    default_database_id: str | None = None,
    default_database_name: str | None = None,
) -> AiboardNotionConnection:
    connection = await get_active_aiboard_notion_connection(
        session=session,
        aiboard_meeting_id=aiboard_meeting_id,
    )
    if connection is None:
        connection = AiboardNotionConnection(
            aiboard_meeting_id=aiboard_meeting_id,
            notion_workspace_id=notion_workspace_id,
            notion_workspace_name=notion_workspace_name,
            bot_id=bot_id,
            access_token=access_token,
            default_database_id=default_database_id,
            default_database_name=default_database_name,
            status=ACTIVE_CONNECTION_STATUS,
            is_deleted=False,
        )
        session.add(connection)
    else:
        connection.notion_workspace_id = notion_workspace_id
        connection.notion_workspace_name = notion_workspace_name
        connection.bot_id = bot_id
        connection.access_token = access_token
        if default_database_id is not None:
            connection.default_database_id = default_database_id
        if default_database_name is not None:
            connection.default_database_name = default_database_name
        connection.status = ACTIVE_CONNECTION_STATUS
        connection.is_deleted = False

    await session.flush()
    await session.refresh(connection)
    return connection


async def set_aiboard_notion_default_database(
    *,
    session: AsyncSession,
    connection: AiboardNotionConnection,
    database_id: str | None,
    database_name: str | None,
) -> AiboardNotionConnection:
    connection.default_database_id = _clean_optional_string(database_id, 255)
    connection.default_database_name = _clean_optional_string(database_name, 255)
    await session.flush()
    await session.refresh(connection)
    return connection


async def revoke_aiboard_notion_connection(
    *,
    session: AsyncSession,
    connection: AiboardNotionConnection,
) -> AiboardNotionConnection:
    connection.status = REVOKED_CONNECTION_STATUS
    connection.is_deleted = True
    await session.flush()
    await session.refresh(connection)
    return connection


async def create_aiboard_notion_oauth_state(
    *,
    session: AsyncSession,
    aiboard_meeting_id: UUID,
    state: str,
    expires_at: datetime,
) -> AiboardNotionOAuthState:
    oauth_state = AiboardNotionOAuthState(
        aiboard_meeting_id=aiboard_meeting_id,
        state=state,
        expires_at=expires_at,
        consumed_at=None,
    )
    session.add(oauth_state)
    await session.flush()
    await session.refresh(oauth_state)
    return oauth_state


async def get_aiboard_notion_oauth_state(
    *,
    session: AsyncSession,
    state: str,
) -> AiboardNotionOAuthState | None:
    result = await session.execute(
        select(AiboardNotionOAuthState)
        .where(AiboardNotionOAuthState.state == state)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def consume_aiboard_notion_oauth_state(
    *,
    session: AsyncSession,
    oauth_state: AiboardNotionOAuthState,
) -> AiboardNotionOAuthState:
    oauth_state.consumed_at = datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(oauth_state)
    return oauth_state


async def list_aiboard_tasks(
    *,
    session: AsyncSession,
    aiboard_meeting_id: UUID,
) -> list[AiboardTask]:
    result = await session.execute(
        select(AiboardTask)
        .where(
            AiboardTask.aiboard_meeting_id == aiboard_meeting_id,
            AiboardTask.is_deleted.is_(False),
        )
        .order_by(AiboardTask.updated_at.desc(), AiboardTask.created_at.desc())
    )
    return list(result.scalars().all())


async def get_aiboard_task(
    *,
    session: AsyncSession,
    task_id: UUID,
) -> AiboardTask | None:
    result = await session.execute(
        select(AiboardTask).where(
            AiboardTask.id == task_id,
            AiboardTask.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def create_aiboard_task(
    *,
    session: AsyncSession,
    aiboard_meeting_id: UUID,
    title: str,
    body: str,
    source_payload: dict[str, Any] | None = None,
    assignee_name: str | None = None,
    status: str = "todo",
    due_at: datetime | None = None,
) -> AiboardTask:
    task = AiboardTask(
        aiboard_meeting_id=aiboard_meeting_id,
        source_payload=source_payload,
        title=_normalize_required_string(title, 255, "Untitled task"),
        body=_normalize_required_string(body, 20000, title),
        assignee_name=_clean_optional_string(assignee_name, 255),
        status=_validate_task_status(status),
        due_at=due_at,
        is_deleted=False,
    )
    session.add(task)
    await session.flush()
    await session.refresh(task)
    return task


async def update_aiboard_task(
    *,
    session: AsyncSession,
    task: AiboardTask,
    title: str | None = None,
    body: str | None = None,
    source_payload: dict[str, Any] | None = None,
    assignee_name: str | None = None,
    status: str | None = None,
    due_at: datetime | None = None,
) -> AiboardTask:
    if title is not None:
        task.title = _normalize_required_string(title, 255, task.title)
    if body is not None:
        task.body = _normalize_required_string(body, 20000, task.body)
    if source_payload is not None:
        task.source_payload = source_payload
    if assignee_name is not None:
        task.assignee_name = _clean_optional_string(assignee_name, 255)
    if status is not None:
        task.status = _validate_task_status(status)
    if due_at is not None:
        task.due_at = due_at

    await session.flush()
    await session.refresh(task)
    return task


async def soft_delete_aiboard_task(
    *,
    session: AsyncSession,
    task: AiboardTask,
) -> AiboardTask:
    task.is_deleted = True
    await session.flush()
    await session.refresh(task)
    return task


async def create_aiboard_notion_sync_log(
    *,
    session: AsyncSession,
    task: AiboardTask,
    connection: AiboardNotionConnection,
    status: str,
    notion_page_id: str | None = None,
    error_message: str | None = None,
) -> AiboardNotionSyncLog:
    sync_log = AiboardNotionSyncLog(
        aiboard_meeting_id=task.aiboard_meeting_id,
        aiboard_task_id=task.id,
        aiboard_notion_connection_id=connection.id,
        notion_page_id=_clean_optional_string(notion_page_id, 255),
        status=_validate_sync_status(status),
        error_message=_clean_optional_string(error_message, 1000),
        is_deleted=False,
    )
    session.add(sync_log)
    await session.flush()
    await session.refresh(sync_log)
    return sync_log


async def mark_aiboard_task_notion_synced(
    *,
    session: AsyncSession,
    task: AiboardTask,
    notion_page_id: str,
    synced_at: datetime | None = None,
) -> AiboardTask:
    task.notion_page_id = _clean_optional_string(notion_page_id, 255)
    task.notion_last_synced_at = synced_at or datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(task)
    return task


def is_oauth_state_usable(
    *,
    expires_at: datetime,
    consumed_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(timezone.utc)
    normalized_expires_at = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    return consumed_at is None and normalized_expires_at > current


def _validate_task_status(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = "in_progress" if normalized == "doing" else normalized
    if normalized not in VALID_TASK_STATUSES:
        return "todo"
    return normalized


def _validate_sync_status(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {SUCCESS_SYNC_STATUS, FAILED_SYNC_STATUS}:
        return FAILED_SYNC_STATUS
    return normalized


def _normalize_required_string(value: str, max_length: int, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    return text[:max_length]


def _clean_optional_string(value: str | None, max_length: int) -> str | None:
    text = str(value or "").strip()
    return text[:max_length] if text else None
