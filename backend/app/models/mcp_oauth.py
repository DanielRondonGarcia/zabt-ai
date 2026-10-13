# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Database models for the MCP OAuth public-client flow."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel


class MCPClientRegistration(SQLModel, table=True):
    """A public OAuth client registered for the MCP resource.

    Public clients never receive a client secret. JSON metadata is stored as
    text so the model remains usable by the repository's SQLite unit tests as
    well as PostgreSQL.
    """

    __tablename__ = "mcpclientregistration"
    __table_args__ = (
        UniqueConstraint("client_id", name="uq_mcpclientregistration_client_id"),
        CheckConstraint("length(trim(client_name)) > 0", name="ck_mcpclientregistration_name"),
        CheckConstraint("length(trim(redirect_uris_json)) > 2", name="ck_mcpclientregistration_redirects"),
        Index("ix_mcpclientregistration_active", "revoked_at", "created_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(sa_column=Column(String(128), nullable=False, index=True))
    client_name: str = Field(sa_column=Column(String(100), nullable=False))
    redirect_uris_json: str = Field(sa_column=Column(Text, nullable=False))
    grant_types_json: str = Field(sa_column=Column(Text, nullable=False))
    scope: str = Field(sa_column=Column(String(64), nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )
    revoked_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, index=True),
    )


class MCPGrant(SQLModel, table=True):
    """A user's consent grant for one client and exact MCP resource."""

    __tablename__ = "mcpgrant"
    __table_args__ = (
        UniqueConstraint(
            "client_registration_id",
            "user_id",
            "resource",
            name="uq_mcpgrant_client_user_resource",
        ),
        CheckConstraint("generation >= 0", name="ck_mcpgrant_generation_nonnegative"),
        Index("ix_mcpgrant_user_lifecycle", "user_id", "revoked_at", "created_at"),
        Index("ix_mcpgrant_client_lifecycle", "client_registration_id", "revoked_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    client_registration_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("mcpclientregistration.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    resource: str = Field(sa_column=Column(String(2048), nullable=False))
    scope: str = Field(sa_column=Column(String(64), nullable=False))
    generation: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0, server_default="0"),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )
    last_used_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
    )
    revoked_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, index=True),
    )


class MCPAccessToken(SQLModel, table=True):
    """An opaque, short-lived OAuth access token stored only as a digest."""

    __tablename__ = "mcpaccesstoken"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_mcpaccesstoken_token_hash"),
        Index("ix_mcpaccesstoken_lifecycle", "grant_id", "revoked_at", "expires_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    grant_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("mcpgrant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    token_hash: str = Field(sa_column=Column(String(64), nullable=False))
    token_prefix: str = Field(sa_column=Column(String(32), nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )
    last_used_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
    )
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False, index=True))
    revoked_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, index=True),
    )


class MCPRefreshToken(SQLModel, table=True):
    """A rotating, one-time OAuth refresh token stored only as a digest."""

    __tablename__ = "mcprefreshtoken"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_mcprefreshtoken_token_hash"),
        Index("ix_mcprefreshtoken_lifecycle", "grant_id", "revoked_at", "expires_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    grant_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("mcpgrant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    token_hash: str = Field(sa_column=Column(String(64), nullable=False))
    token_prefix: str = Field(sa_column=Column(String(32), nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False, index=True))
    used_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, index=True),
    )
    revoked_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, index=True),
    )
