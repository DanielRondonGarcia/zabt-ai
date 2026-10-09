# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Persistence for AI chat conversations and their bounded memory window."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.db.engine import engine
from app.models.ai_chat import (
    CONVERSATION_TITLE_MAX_CHARS,
    MESSAGE_ROLES,
    AIChatConversation,
    AIChatConversationDetail,
    AIChatConversationSummary,
    AIChatMessage,
    AIChatMessageRead,
)
from app.services.base import BaseService
from app.services.group import group_service as default_group_service

DEFAULT_MEMORY_MAX_TURNS = 6
DEFAULT_MEMORY_MAX_CHARS = 4000
_SOURCE_KEYS = ("meeting_id", "kind", "chunk_index", "score", "text")
_TITLE_ELLIPSIS = "…"


def derive_conversation_title(first_message: str) -> str:
    """Build a bounded, single-line title from the first user message."""

    collapsed = " ".join(first_message.split())
    if not collapsed:
        return "New conversation"
    if len(collapsed) <= CONVERSATION_TITLE_MAX_CHARS:
        return collapsed
    return collapsed[: CONVERSATION_TITLE_MAX_CHARS - len(_TITLE_ELLIPSIS)].rstrip() + _TITLE_ELLIPSIS


def normalize_sources(sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep only the citation fields we are allowed to persist."""

    normalized: list[dict[str, Any]] = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        normalized.append({key: source.get(key) for key in _SOURCE_KEYS})
    return normalized


def bound_history(
    messages: list[dict[str, Any]],
    *,
    max_turns: int = DEFAULT_MEMORY_MAX_TURNS,
    max_chars: int = DEFAULT_MEMORY_MAX_CHARS,
) -> list[dict[str, str]]:
    """Return the most recent ``max_turns`` messages that fit in ``max_chars``.

    A turn is one stored message. ``messages`` must be ordered oldest-first; the
    result keeps that order so callers can splice it directly into a prompt.
    Messages with unknown roles or empty content are skipped.
    """

    if max_turns <= 0 or max_chars <= 0:
        return []
    window: list[dict[str, str]] = []
    remaining = max_chars
    for message in reversed(messages):
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        if role not in MESSAGE_ROLES or not content:
            continue
        if len(content) > remaining:
            break
        window.append({"role": role, "content": content})
        remaining -= len(content)
        if len(window) >= max_turns:
            break
    window.reverse()
    return window


class AIChatConversationService(BaseService):
    """Repository for owner-scoped conversations within an authorized group."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        group_service=default_group_service,
    ):
        self._session_factory = session_factory or (lambda: Session(engine))
        self.group_service = group_service

    def _session(self) -> Session:
        return self._session_factory()

    def _authorize(
        self, conversation: AIChatConversation | None, owner_id: int
    ) -> AIChatConversation:
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
        if conversation.owner_id != owner_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
        # Ownership is not sufficient for a conversation whose group access was
        # revoked after the conversation was created.
        self.group_service.get_accessible(conversation.group_id, owner_id)
        return conversation

    def list_for_group(self, owner_id: int, group_id: int) -> list[AIChatConversationSummary]:
        with self._session() as session:
            count_column = func.count(AIChatMessage.id).label("message_count")
            statement = (
                select(AIChatConversation, count_column)
                .outerjoin(AIChatMessage, AIChatMessage.conversation_id == AIChatConversation.id)
                .where(AIChatConversation.owner_id == owner_id)
                .where(AIChatConversation.group_id == group_id)
                .group_by(AIChatConversation.id)
                .order_by(AIChatConversation.updated_at.desc(), AIChatConversation.id.desc())
            )
            rows = session.exec(statement).all()
            return [self._to_summary(conversation, int(count)) for conversation, count in rows]

    def get_owned(self, conversation_id: int, owner_id: int) -> AIChatConversation:
        with self._session() as session:
            return self._authorize(session.get(AIChatConversation, conversation_id), owner_id)

    def get_detail(self, conversation_id: int, owner_id: int) -> AIChatConversationDetail:
        with self._session() as session:
            conversation = self._authorize(session.get(AIChatConversation, conversation_id), owner_id)
            messages = session.exec(
                select(AIChatMessage)
                .where(AIChatMessage.conversation_id == conversation.id)
                .order_by(AIChatMessage.id)
            ).all()
            summary = self._to_summary(conversation, len(messages))
            return AIChatConversationDetail(
                **summary.model_dump(),
                messages=[AIChatMessageRead.model_validate(message) for message in messages],
            )

    def create(self, owner_id: int, group_id: int, first_message: str) -> AIChatConversation:
        conversation = AIChatConversation(
            owner_id=owner_id,
            group_id=group_id,
            title=derive_conversation_title(first_message),
        )
        with self._session() as session:
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            session.expunge(conversation)
        return conversation

    def append_turn(
        self,
        conversation_id: int,
        *,
        user_message: str,
        assistant_answer: str,
        sources: list[dict[str, Any]] | None,
        evidence_status: str | None,
    ) -> None:
        """Persist one user/assistant exchange atomically and bump ``updated_at``."""

        now = datetime.utcnow()
        with self._session() as session:
            conversation = session.get(AIChatConversation, conversation_id)
            if conversation is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
            session.add(
                AIChatMessage(
                    conversation_id=conversation_id,
                    role="user",
                    content=user_message,
                    sources=[],
                    evidence_status=None,
                    created_at=now,
                )
            )
            session.add(
                AIChatMessage(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=assistant_answer,
                    sources=normalize_sources(sources),
                    evidence_status=evidence_status,
                    created_at=now,
                )
            )
            conversation.updated_at = now
            session.add(conversation)
            session.commit()

    def delete(self, conversation_id: int, owner_id: int) -> None:
        with self._session() as session:
            conversation = self._authorize(session.get(AIChatConversation, conversation_id), owner_id)
            session.delete(conversation)
            session.commit()

    def recent_turns(
        self,
        conversation_id: int,
        *,
        max_turns: int = DEFAULT_MEMORY_MAX_TURNS,
        max_chars: int = DEFAULT_MEMORY_MAX_CHARS,
    ) -> list[dict[str, str]]:
        """Return the bounded tail of prior messages, oldest-first."""

        with self._session() as session:
            statement = (
                select(AIChatMessage.role, AIChatMessage.content)
                .where(AIChatMessage.conversation_id == conversation_id)
                .order_by(AIChatMessage.id.desc())
                .limit(max(max_turns, 0))
            )
            newest_first = session.exec(statement).all()
        oldest_first = [{"role": role, "content": content} for role, content in reversed(newest_first)]
        return bound_history(oldest_first, max_turns=max_turns, max_chars=max_chars)

    @staticmethod
    def _to_summary(conversation: AIChatConversation, message_count: int) -> AIChatConversationSummary:
        return AIChatConversationSummary(
            id=conversation.id,
            group_id=conversation.group_id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            message_count=message_count,
        )


ai_chat_conversation_service = AIChatConversationService()
