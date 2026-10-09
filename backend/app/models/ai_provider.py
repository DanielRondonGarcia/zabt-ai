# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Per-user AI provider configuration models."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlmodel import Field, SQLModel


class CustomAIProvider(str, Enum):
    """Supported user-owned completion providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class AIProviderConfiguration(SQLModel, table=True):
    """One optional completion-provider configuration owned by one user."""

    __tablename__ = "aiproviderconfiguration"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_aiproviderconfiguration_user_id"),
        CheckConstraint(
            "provider IN ('openai', 'anthropic', 'ollama')",
            name="ck_aiproviderconfiguration_provider",
        ),
        Index("ix_aiproviderconfiguration_enabled", "enabled"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider: CustomAIProvider = Field(
        sa_column=Column(String(length=32), nullable=False)
    )
    model: str = Field(sa_column=Column(String(length=128), nullable=False))
    base_url: Optional[str] = Field(
        default=None,
        sa_column=Column(String(length=2048), nullable=True),
    )
    encrypted_api_key: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true"),
    )
    use_for_summary: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true"),
    )
    use_for_chat: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true"),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(), nullable=False, server_default=func.now()),
    )
