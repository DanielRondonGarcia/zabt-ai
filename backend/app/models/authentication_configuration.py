# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Singleton configuration for regular-user authentication mode."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, func
from sqlmodel import Field, SQLModel


class UserLoginMode(str, Enum):
    LOCAL = "local"
    MICROSOFT_OIDC = "microsoft_oidc"


class AuthenticationConfiguration(SQLModel, table=True):
    """The global sign-in mode for non-superuser accounts."""

    __tablename__ = "authenticationconfiguration"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_authenticationconfiguration_singleton_id"),
        CheckConstraint(
            "user_login_mode IN ('local', 'microsoft_oidc')",
            name="ck_authenticationconfiguration_user_login_mode",
        ),
        Index("ix_authenticationconfiguration_updated_by", "updated_by"),
    )

    id: int = Field(default=1, primary_key=True)
    user_login_mode: UserLoginMode = Field(
        default=UserLoginMode.LOCAL,
        sa_column=Column(String(length=32), nullable=False, server_default="local"),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(), nullable=False, server_default=func.now()),
    )
    updated_by: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
