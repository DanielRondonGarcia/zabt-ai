# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Retrieval-grounded AI chat endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.api import deps
from app.models import User
from app.services.ai_chat import ai_chat_service

router = APIRouter()


class AIChatRequest(BaseModel):
    group_id: int
    message: str = Field(..., min_length=1, max_length=4000)
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class AIChatSource(BaseModel):
    meeting_id: int
    kind: str
    chunk_index: int
    score: float
    text: str


class AIChatResponse(BaseModel):
    group_id: int
    answer: str
    sources: list[AIChatSource]


@router.post("/", response_model=AIChatResponse)
def chat(
    *,
    payload: AIChatRequest,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    return ai_chat_service.chat(
        group_id=payload.group_id,
        user_id=current_user.id,
        message=payload.message,
        limit=payload.limit,
    )
