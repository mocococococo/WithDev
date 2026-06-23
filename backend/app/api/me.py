from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, verify_firebase_user
from app.db.session import get_db_session
from app.services.user_initialization_service import initialize_user_context


router = APIRouter()


class MeUser(BaseModel):
    id: UUID
    firebase_uid: str
    email: str
    display_name: str
    photo_url: str | None


class MeTeam(BaseModel):
    id: UUID
    name: str
    role: str
    member_count: int


class MeResponse(BaseModel):
    user: MeUser
    teams: list[MeTeam]


@router.get("/me", response_model=MeResponse)
async def get_me(
    auth_user: AuthenticatedUser = Depends(verify_firebase_user),
    session: AsyncSession = Depends(get_db_session),
) -> MeResponse:
    context = await initialize_user_context(session=session, auth_user=auth_user)

    return MeResponse(
        user=MeUser(
            id=context.user.id,
            firebase_uid=context.user.firebase_uid,
            email=context.user.email,
            display_name=context.user.display_name,
            photo_url=context.user.photo_url,
        ),
        teams=[
            MeTeam(id=team.id, name=team.name, role=team.role, member_count=team.member_count)
            for team in context.teams
        ],
    )
