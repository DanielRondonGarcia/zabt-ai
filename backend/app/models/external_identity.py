# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Persistent identities issued by external authentication providers."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


class ExternalIdentityProvider(str, Enum):
    MICROSOFT = "microsoft"


class ExternalIdentity(SQLModel, table=True):
    """An external subject linked to one local Zabt user.

    Authentication tokens are deliberately not part of this table. Provider
    tokens belong to the existing integration model and are never used as
    application login credentials.
    """

    __tablename__ = "externalidentity"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "tenant_id",
            "subject",
            name="uq_externalidentity_provider_tenant_subject",
        ),
        Index("ix_externalidentity_user_provider", "user_id", "provider"),
        Index("ix_externalidentity_email", "email"),
        Index("ix_externalidentity_tenant_id", "tenant_id"),
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
    provider: ExternalIdentityProvider = Field(
        sa_column=Column(String(length=32), nullable=False)
    )
    subject: str = Field(sa_column=Column(String(length=255), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(length=255), nullable=False))
    email: str | None = Field(
        default=None,
        sa_column=Column(String(length=320), nullable=True),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: datetime = Field(default_factory=datetime.utcnow)

    user: "User" = Relationship(back_populates="external_identities")
