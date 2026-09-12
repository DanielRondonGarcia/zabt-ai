# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from typing import Generator
from fastapi import Depends, HTTPException, status
from sqlmodel import Session

from app.core import security
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
