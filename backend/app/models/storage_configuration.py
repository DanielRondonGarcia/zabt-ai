# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Global object-storage configuration and its public API schemas."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, Text, func
from sqlmodel import Field, SQLModel


class StorageProvider(str, Enum):
    MINIO = "minio"
    AWS_S3 = "aws_s3"


class StorageConfiguration(SQLModel, table=True):
    """Singleton settings for the object-storage provider used by new uploads."""

    __tablename__ = "storageconfiguration"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_storageconfiguration_singleton"),
        CheckConstraint(
            "provider IN ('minio', 'aws_s3')",
            name="ck_storageconfiguration_provider",
        ),
    )

    id: int = Field(
        default=1,
        sa_column=Column(Integer, primary_key=True, server_default="1"),
    )
    provider: StorageProvider = Field(
        default=StorageProvider.MINIO,
        sa_column=Column(String(length=32), nullable=False, server_default="minio"),
    )
    aws_region: Optional[str] = Field(
        default=None,
        sa_column=Column(String(length=64), nullable=True),
    )
    aws_bucket: Optional[str] = Field(
        default=None,
        sa_column=Column(String(length=255), nullable=True),
    )
    aws_access_key_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(length=256), nullable=True),
    )
    encrypted_aws_secret_access_key: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(), nullable=False, server_default=func.now()),
    )


class StorageConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: StorageProvider
    aws_region: Optional[str] = Field(default=None, max_length=64)
    aws_bucket: Optional[str] = Field(default=None, max_length=255)
    aws_access_key_id: Optional[str] = Field(default=None, max_length=256)
    aws_secret_access_key: Optional[SecretStr] = None

    @field_validator("aws_region", "aws_bucket", "aws_access_key_id")
    @classmethod
    def trim_optional_values(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("aws_secret_access_key")
    @classmethod
    def trim_secret_value(cls, value: Optional[SecretStr]) -> Optional[SecretStr]:
        if value is None:
            return None
        return SecretStr(value.get_secret_value().strip())


class StorageConfigurationRead(BaseModel):
    provider: StorageProvider
    aws_region: Optional[str] = None
    aws_bucket: Optional[str] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key_configured: bool
