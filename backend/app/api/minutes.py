from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.auth import AuthenticatedUser, verify_firebase_user
from app.services.minutes_service import (
    MAX_TEXT_LENGTH,
    MinutesGenerationError,
    generate_minutes_from_text,
)


router = APIRouter()


class MinutesFromTextRequest(BaseModel):
    text: str | None = None


class MinutesBody(BaseModel):
    body: str


class MinutesFromTextResponse(BaseModel):
    minutes: MinutesBody


@router.post("/from-text", response_model=MinutesFromTextResponse)
async def minutes_from_text(
    request: MinutesFromTextRequest | None = None,
    _user: AuthenticatedUser = Depends(verify_firebase_user),
) -> MinutesFromTextResponse:
    text = request.text if request else ""
    text = text or ""
    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text is required",
        )

    if len(text) > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text must be 50000 characters or less",
        )

    try:
        body = await run_in_threadpool(generate_minutes_from_text, text.strip())
    except MinutesGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to generate minutes",
        ) from exc

    return MinutesFromTextResponse(minutes=MinutesBody(body=body))
