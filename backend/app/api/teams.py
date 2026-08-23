from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, verify_firebase_user
from app.core.config import get_settings
from app.db.session import get_db_session
from app.services.aiboard_service import (
    AiboardAuthenticationError,
    AiboardConfigurationError,
    AiboardRequestError,
)
from app.services.demo_mode_service import create_demo_mode_workspace
from app.services.team_invite_service import (
    InviteSummary,
    TeamSummary,
    accept_team_invite,
    create_team,
    create_team_invite,
    delete_team,
    get_invite_preview,
    list_team_invites,
    revoke_team_invite,
)

router = APIRouter()
settings = get_settings()


class TeamResponse(BaseModel):
    id: UUID
    name: str
    role: str
    member_count: int


class CreateTeamRequest(BaseModel):
    name: str = Field(max_length=255)


class CreateTeamResponse(BaseModel):
    team: TeamResponse


class DeleteTeamRequest(BaseModel):
    name: str = Field(max_length=255)


class InviteCreatorResponse(BaseModel):
    id: UUID
    name: str


class InviteResponse(BaseModel):
    id: UUID
    team_id: UUID
    created_by: InviteCreatorResponse
    created_at: datetime
    expires_at: datetime
    status: str
    can_revoke: bool


class InviteListResponse(BaseModel):
    invites: list[InviteResponse]


class CreateInviteResponse(BaseModel):
    invite: InviteResponse
    invite_url: str


class InvitePreviewResponse(BaseModel):
    team_id: UUID
    team_name: str
    expires_at: datetime
    already_member: bool


class AcceptInviteResponse(BaseModel):
    team: TeamResponse


def to_team_response(team: TeamSummary) -> TeamResponse:
    return TeamResponse(id=team.id, name=team.name, role=team.role, member_count=team.member_count)


def to_invite_response(invite: InviteSummary) -> InviteResponse:
    return InviteResponse(
        id=invite.id,
        team_id=invite.team_id,
        created_by=InviteCreatorResponse(id=invite.created_by_user_id, name=invite.created_by_name),
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        status=invite.status,
        can_revoke=invite.can_revoke,
    )


@router.post("/teams", response_model=CreateTeamResponse, status_code=status.HTTP_201_CREATED)
async def post_team(
    request: CreateTeamRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> CreateTeamResponse:
    team = await create_team(session=session, auth_user=auth_user, name=request.name)
    return CreateTeamResponse(team=to_team_response(team))


@router.post("/teams/demo", response_model=CreateTeamResponse, status_code=status.HTTP_201_CREATED)
async def post_demo_team(
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> CreateTeamResponse:
    host_email = (auth_user.email or auth_user.claims.get("email") or "").strip()
    if not host_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="host email is required",
        )
    try:
        result = await create_demo_mode_workspace(
            session=session,
            auth_user=auth_user,
            aiboard_api_base_url=settings.aiboard_api_base_url,
            aiboard_api_key=settings.aiboard_api_key,
        )
    except AiboardConfigurationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AiboardAuthenticationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except AiboardRequestError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return CreateTeamResponse(team=to_team_response(result.team))


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team_endpoint(
    team_id: UUID,
    request: DeleteTeamRequest,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    await delete_team(
        session=session,
        auth_user=auth_user,
        team_id=team_id,
        confirmation_name=request.name,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/teams/{team_id}/invites", response_model=InviteListResponse)
async def get_team_invites(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> InviteListResponse:
    invites = await list_team_invites(session=session, auth_user=auth_user, team_id=team_id)
    return InviteListResponse(invites=[to_invite_response(invite) for invite in invites])


@router.post("/teams/{team_id}/invites", response_model=CreateInviteResponse, status_code=status.HTTP_201_CREATED)
async def post_team_invite(
    team_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> CreateInviteResponse:
    invite, token = await create_team_invite(session=session, auth_user=auth_user, team_id=team_id)
    return CreateInviteResponse(
        invite=to_invite_response(invite),
        invite_url=f"{settings.frontend_base_url.rstrip('/')}/invite/{token}",
    )


@router.delete("/teams/{team_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team_invite(
    team_id: UUID,
    invite_id: UUID,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    await revoke_team_invite(session=session, auth_user=auth_user, team_id=team_id, invite_id=invite_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/invites/{token}", response_model=InvitePreviewResponse)
async def get_invite(
    token: str,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> InvitePreviewResponse:
    preview = await get_invite_preview(session=session, auth_user=auth_user, token=token)
    return InvitePreviewResponse(
        team_id=preview.team_id,
        team_name=preview.team_name,
        expires_at=preview.expires_at,
        already_member=preview.already_member,
    )


@router.post("/invites/{token}/accept", response_model=AcceptInviteResponse)
async def post_invite_accept(
    token: str,
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> AcceptInviteResponse:
    team = await accept_team_invite(session=session, auth_user=auth_user, token=token)
    return AcceptInviteResponse(team=to_team_response(team))
