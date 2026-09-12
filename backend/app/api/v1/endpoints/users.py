# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Authenticated user profile endpoints."""

from fastapi import APIRouter, Depends

from app.api.deps import get_current_active_user
from app.api.v1.endpoints.auth import UserRead
from app.models import User


router = APIRouter()


@router.get("/me", response_model=UserRead)
def get_current_user_profile(user: User = Depends(get_current_active_user)) -> UserRead:
    return UserRead.model_validate(user, from_attributes=True)
