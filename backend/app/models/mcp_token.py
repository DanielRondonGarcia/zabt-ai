# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Database model for user-managed remote MCP bearer tokens."""

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlmodel import Field, SQLModel


class MCPToken(SQLModel, table=True):
    """A revocable, owner-scoped token for the read-only MCP surface.

    The raw bearer token is intentionally not a model field. Only its SHA-256
    digest is persisted, while ``token_prefix`` is safe display metadata.
    """

    __tablename__ = "mcptoken"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_mcptoken_token_hash"),
        CheckConstraint("length(trim(label)) > 0", name="ck_mcptoken_label_nonempty"),
        Index(
            "ix_mcptoken_user_lifecycle",
            "user_id",
            "revoked_at",
            "expires_at",
        ),
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
    label: str = Field(
        sa_column=Column(String(100), nullable=False)
    )
    token_hash: str = Field(
        sa_column=Column(String(64), nullable=False)
    )
    token_prefix: str = Field(
        sa_column=Column(String(24), nullable=False)
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )
    last_used_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False, index=True)
    )
    revoked_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, index=True),
    )
