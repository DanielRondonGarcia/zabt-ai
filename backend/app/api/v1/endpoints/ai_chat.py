# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Retrieval-grounded AI chat endpoint with persistent conversations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field, field_validator

from app.api import deps
from app.models import AIChatConversationDetail, AIChatConversationSummary, User
from app.services.ai_chat import EvidenceStatus, ai_chat_service
from app.services.ai_chat_conversations import ai_chat_conversation_service
from app.services.group import group_service

router = APIRouter()


class AIChatRequest(BaseModel):
    group_id: int
    message: str = Field(..., min_length=1, max_length=4000)
    limit: int = Field(default=8, ge=1, le=20)
    conversation_id: int | None = Field(default=None, ge=1)

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
    conversation_id: int
    group_id: int
    answer: str
    sources: list[AIChatSource]
    evidence_status: EvidenceStatus


def _authorize_conversation_access(conversation_id: int, user_id: int) -> None:
    """Revalidate both conversation ownership and its current group access."""
    conversation = ai_chat_conversation_service.get_owned(conversation_id, user_id)
    group_service.get_accessible(conversation.group_id, user_id)


@router.post("/", response_model=AIChatResponse)
def chat(
    *,
    payload: AIChatRequest,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Answer one message; the service validates conversation ownership (404/403) and group match (409)."""
    return ai_chat_service.chat(
        group_id=payload.group_id,
        user_id=current_user.id,
        message=payload.message,
        limit=payload.limit,
        conversation_id=payload.conversation_id,
    )


@router.get("/conversations", response_model=list[AIChatConversationSummary])
def list_conversations(
    *,
    group_id: int = Query(..., ge=1),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    group_service.get_accessible(group_id, current_user.id)
    return ai_chat_conversation_service.list_for_group(owner_id=current_user.id, group_id=group_id)


@router.get("/conversations/{conversation_id}", response_model=AIChatConversationDetail)
def get_conversation(
    *,
    conversation_id: int,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    _authorize_conversation_access(conversation_id, current_user.id)
    return ai_chat_conversation_service.get_detail(conversation_id, current_user.id)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    *,
    conversation_id: int,
    current_user: User = Depends(deps.get_current_active_user),
) -> Response:
    _authorize_conversation_access(conversation_id, current_user.id)
    ai_chat_conversation_service.delete(conversation_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
