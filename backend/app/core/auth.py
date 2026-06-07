from dataclasses import dataclass
from typing import Any

import firebase_admin
from fastapi import Header, HTTPException, status
from firebase_admin import auth as firebase_auth


@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    email: str | None
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
