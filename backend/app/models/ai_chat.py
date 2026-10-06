# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Persistent AI chat conversations scoped to one owner and one group."""

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

CONVERSATION_TITLE_MAX_CHARS = 120
MESSAGE_ROLES = ("user", "assistant")

# Portable JSON column: JSONB on PostgreSQL, plain JSON elsewhere (e.g. SQLite in tests).
_SOURCES_JSON = JSON().with_variant(JSONB(), "postgresql")


class AIChatConversation(SQLModel, table=True):
    __tablename__ = "aichatconversation"
    __table_args__ = (
        Index("ix_aichatconversation_owner_group_updated", "owner_id", "group_id", "updated_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    group_id: int = Field(
        sa_column=Column(Integer, ForeignKey("group.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    title: str = Field(sa_column=Column(String(CONVERSATION_TITLE_MAX_CHARS), nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    messages: List["AIChatMessage"] = Relationship(
        back_populates="conversation",
        sa_relationship_kwargs={
            # ORM-level cascade mirrors the DB ON DELETE CASCADE so deleting a
            # conversation removes its messages on any backend.
            "cascade": "all, delete-orphan",
            "order_by": "AIChatMessage.id",
        },
    )


class AIChatMessage(SQLModel, table=True):
    __tablename__ = "aichatmessage"

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("aichatconversation.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    role: str = Field(sa_column=Column(String(16), nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
    # Cited retrieval sources: list of {meeting_id, kind, chunk_index, score, text}.
    sources: List[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(_SOURCES_JSON, nullable=False)
    )
    evidence_status: Optional[str] = Field(default=None, sa_column=Column(String(32), nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)

    conversation: Optional[AIChatConversation] = Relationship(back_populates="messages")


class AIChatSourceRead(BaseModel):
    meeting_id: int
    kind: str
    chunk_index: int
    score: float
    text: str


class AIChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    sources: list[AIChatSourceRead]
    evidence_status: Optional[str]
    created_at: datetime


class AIChatConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: int
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class AIChatConversationDetail(AIChatConversationSummary):
    messages: list[AIChatMessageRead]
