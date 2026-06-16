from dataclasses import dataclass
from typing import Any

import firebase_admin
from fastapi import Header, HTTPException, status
from firebase_admin import auth as firebase_auth
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token

from app.core.config import get_settings


@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    email: str | None
    claims: dict[str, Any]


@dataclass(frozen=True)
class AiboardServiceAccount:
    email: str
    claims: dict[str, Any]


def _ensure_firebase_app() -> None:
    if not firebase_admin._apps:
        firebase_admin.initialize_app()


def verify_firebase_user(authorization: str | None = Header(default=None)) -> AuthenticatedUser:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authorization bearer token is required",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authorization bearer token is required",
        )

    try:
        _ensure_firebase_app()
        decoded_token = firebase_auth.verify_id_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid firebase id token",
        ) from exc

    return AuthenticatedUser(
        uid=decoded_token["uid"],
        email=decoded_token.get("email"),
        claims=decoded_token,
    )


def verify_aiboard_service_account(
    authorization: str | None = Header(default=None),
) -> AiboardServiceAccount:
    settings = get_settings()
    if not settings.aiboard_allowed_service_account or not settings.aiboard_expected_audience:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="aiboard auth is not configured",
        )

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authorization bearer token is required",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authorization bearer token is required",
        )

    try:
        decoded_token = id_token.verify_oauth2_token(
            token,
            GoogleAuthRequest(),
            settings.aiboard_expected_audience,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid aiboard id token",
        ) from exc

    email = decoded_token.get("email")
    if email != settings.aiboard_allowed_service_account:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="aiboard service account is forbidden",
        )

    return AiboardServiceAccount(email=email, claims=decoded_token)
