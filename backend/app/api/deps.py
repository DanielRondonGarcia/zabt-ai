# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from typing import Generator
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.db.engine import engine
from app.models import User


def get_db() -> Generator:
    with Session(engine) as session:
        yield session


def get_current_user(
    token_payload: dict = Depends(security.get_token_payload),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the local user identified by the validated access token."""

    return _resolve_user(token_payload, db)


def _resolve_user(token_payload: dict, db: Session) -> User:
    try:
        user_id = int(token_payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user")
    return current_user


def get_optional_current_active_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security.bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Resolve an active caller when credentials are present, otherwise return None."""

    if credentials is None and not request.cookies.get(settings.AUTH_ACCESS_COOKIE_NAME):
        return None
    try:
        token_payload = security.get_token_payload(request, credentials)
        current_user = _resolve_user(token_payload, db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return None
        raise
    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user")
    return current_user
