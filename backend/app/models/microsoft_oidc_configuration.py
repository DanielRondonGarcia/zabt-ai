# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Global, public Microsoft Entra OIDC configuration."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Index, String, UniqueConstraint
from sqlmodel import Field, SQLModel


class MicrosoftOidcProvider(str, Enum):
    MICROSOFT = "microsoft"


class MicrosoftOidcConfiguration(SQLModel, table=True):
    """The single public-client OIDC configuration for a Zabt instance.

    This table deliberately contains no client secret. Microsoft Entra SPA
    clients use authorization code + PKCE, while delegated Graph OAuth keeps
    its confidential credentials in deployment-managed settings.
    """

    __tablename__ = "microsoftoidcconfiguration"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            name="uq_microsoftoidcconfiguration_provider",
        ),
        Index(
            "ix_microsoftoidcconfiguration_enabled",
            "enabled",
        ),
        Index(
            "ix_microsoftoidcconfiguration_updated_by",
            "updated_by",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: MicrosoftOidcProvider = Field(
        default=MicrosoftOidcProvider.MICROSOFT,
        sa_column=Column(String(length=32), nullable=False),
    )
    client_id: str = Field(
        sa_column=Column(String(length=36), nullable=False),
    )
    tenant_id: str = Field(
        sa_column=Column(String(length=64), nullable=False),
    )
    redirect_uri: str = Field(
        sa_column=Column(String(length=2048), nullable=False),
    )
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[int] = Field(
        default=None,
        foreign_key="user.id",
    )
