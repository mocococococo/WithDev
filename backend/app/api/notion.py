from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from app.core.auth import AuthenticatedUser, verify_firebase_user
from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.notion import NotionConnection
from app.services.notion_repository import (
    consume_notion_oauth_state,
    create_notion_oauth_state,
    get_active_notion_connection,
    get_notion_oauth_state,
    is_oauth_state_usable,
    set_notion_default_database,
    upsert_notion_connection,
)
from app.services.notion_service import (
    NOTION_AUTHORIZE_URL,
    NotionApiError,
    exchange_oauth_code,
    list_databases,
)
from app.services.team_access_service import require_team_member


router = APIRouter()
settings = get_settings()
STATE_TTL_MINUTES = 10


class NotionOAuthStartResponse(BaseModel):
    url: str


class NotionConnectionBody(BaseModel):
    connected: bool
    notion_workspace_id: str | None = None
    notion_workspace_name: str | None = None
    default_database_id: str | None = None
    default_database_name: str | None = None


class NotionConnectionResponse(BaseModel):
    connection: NotionConnectionBody


class NotionDatabaseBody(BaseModel):
    id: str
    title: str


class NotionDatabaseListResponse(BaseModel):
    databases: list[NotionDatabaseBody]


class NotionDefaultDatabaseRequest(BaseModel):
    database_id: str | None = None
    database_name: str | None = None


@router.get("/teams/{team_id}/notion/oauth/start", response_model=NotionOAuthStartResponse)
async def start_notion_oauth(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> NotionOAuthStartResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)

    if not settings.notion_client_id or not settings.notion_redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="notion oauth is not configured",
        )

    state_value = token_urlsafe(32)
    await create_notion_oauth_state(
        session=session,
        team_id=team_id,
        state=state_value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=STATE_TTL_MINUTES),
    )
    await session.commit()

    query = urlencode(
        {
            "client_id": settings.notion_client_id,
            "response_type": "code",
            "owner": "user",
            "redirect_uri": settings.notion_redirect_uri,
            "state": state_value,
        }
    )
    return NotionOAuthStartResponse(url=f"{NOTION_AUTHORIZE_URL}?{query}")


@router.get("/notion/oauth/callback")
async def notion_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    oauth_state = await get_notion_oauth_state(session=session, state=state or "") if state else None
    team_id = oauth_state.team_id if oauth_state is not None else None

    if error:
        return _redirect_to_frontend("/notion/error", team_id=team_id, reason=error)
    if not code or oauth_state is None:
        return _redirect_to_frontend("/notion/error", team_id=team_id, reason="invalid_state")
    if not is_oauth_state_usable(
        expires_at=oauth_state.expires_at,
        consumed_at=oauth_state.consumed_at,
    ):
        reason = "state_consumed" if oauth_state.consumed_at is not None else "state_expired"
        return _redirect_to_frontend("/notion/error", team_id=team_id, reason=reason)
    if not settings.notion_client_id or not settings.notion_client_secret or not settings.notion_redirect_uri:
        return _redirect_to_frontend("/notion/error", team_id=team_id, reason="not_configured")

    try:
        token = await exchange_oauth_code(
            client_id=settings.notion_client_id,
            client_secret=settings.notion_client_secret,
            code=code,
            redirect_uri=settings.notion_redirect_uri,
        )
    except NotionApiError:
        return _redirect_to_frontend("/notion/error", team_id=team_id, reason="oauth_failed")

    await upsert_notion_connection(
        session=session,
        team_id=oauth_state.team_id,
        notion_workspace_id=token.notion_workspace_id,
        notion_workspace_name=token.notion_workspace_name,
        bot_id=token.bot_id,
        access_token=token.access_token,
    )
    await consume_notion_oauth_state(session=session, oauth_state=oauth_state)
    await session.commit()

    return _redirect_to_frontend("/notion/success", team_id=oauth_state.team_id)


@router.get("/teams/{team_id}/notion/connection", response_model=NotionConnectionResponse)
async def get_notion_connection(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> NotionConnectionResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)
    connection = await get_active_notion_connection(session=session, team_id=team_id)
    if connection is None:
        return NotionConnectionResponse(connection=NotionConnectionBody(connected=False))
    return NotionConnectionResponse(connection=_notion_connection_body(connection))


@router.get("/teams/{team_id}/notion/databases", response_model=NotionDatabaseListResponse)
async def list_team_notion_databases(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> NotionDatabaseListResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)
    connection = await _get_required_notion_connection(session=session, team_id=team_id)

    try:
        databases = await list_databases(access_token=connection.access_token)
    except NotionApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to fetch notion databases",
        ) from exc

    return NotionDatabaseListResponse(
        databases=[NotionDatabaseBody(id=database.id, title=database.title) for database in databases]
    )


@router.patch("/teams/{team_id}/notion/default-database", response_model=NotionConnectionResponse)
async def update_notion_default_database(
    team_id: UUID,
    request: NotionDefaultDatabaseRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> NotionConnectionResponse:
    await require_team_member(session=session, auth_user=auth_user, team_id=team_id)

    database_id = (request.database_id or "").strip()
    if not database_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="database_id is required",
        )

    connection = await _get_required_notion_connection(session=session, team_id=team_id)
    database_name = (request.database_name or "").strip() or None
    if database_name is None:
        database_name = await _resolve_database_name(
            access_token=connection.access_token,
            database_id=database_id,
        )

    connection = await set_notion_default_database(
        session=session,
        connection=connection,
        database_id=database_id,
        database_name=database_name,
    )
    await session.commit()
    return NotionConnectionResponse(connection=_notion_connection_body(connection))


async def _get_required_notion_connection(
    *,
    session: AsyncSession,
    team_id: UUID,
) -> NotionConnection:
    connection = await get_active_notion_connection(session=session, team_id=team_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notion connection not found",
        )
    return connection


async def _resolve_database_name(*, access_token: str, database_id: str) -> str | None:
    try:
        databases = await list_databases(access_token=access_token)
    except NotionApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to fetch notion databases",
        ) from exc

    for database in databases:
        if database.id == database_id:
            return database.title
    return None


def _redirect_to_frontend(
    path: str,
    *,
    team_id: UUID | None = None,
    reason: str | None = None,
) -> RedirectResponse:
    params: dict[str, str] = {}
    if team_id is not None:
        params["team_id"] = str(team_id)
    if reason:
        params["reason"] = reason

    base_url = settings.frontend_base_url.rstrip("/")
    query = urlencode(params)
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{query}"
    return RedirectResponse(url=url)


def _notion_connection_body(connection: NotionConnection) -> NotionConnectionBody:
    return NotionConnectionBody(
        connected=True,
        notion_workspace_id=connection.notion_workspace_id,
        notion_workspace_name=connection.notion_workspace_name,
        default_database_id=connection.default_database_id,
        default_database_name=connection.default_database_name,
    )