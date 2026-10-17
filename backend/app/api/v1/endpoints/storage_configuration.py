# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""System-superuser API for global object-storage settings."""

from datetime import datetime
from typing import cast

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_current_active_user, get_db
from app.core import security
from app.models import User
from app.models.storage_configuration import (
    StorageConfiguration,
    StorageConfigurationRead,
    StorageConfigurationUpdate,
    StorageProvider,
)
from app.services.storage import S3Storage, get_storage_for_provider
from app.services.storage_configuration import (
    StorageConfigurationSecretStorageError,
    StorageConfigurationValidationError,
    storage_configuration_service,
)

router = APIRouter()
S3_EXPLORER_PAGE_SIZE = 50


class S3ObjectEntry(BaseModel):
    key: str
    size: int
    last_modified: datetime | None = None


class S3ObjectPage(BaseModel):
    objects: list[S3ObjectEntry]
    prefixes: list[str]
    next_continuation_token: str | None = None


def _require_system_superuser(current_user: User) -> None:
    if not current_user.is_superuser or current_user.id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System superuser access is required",
        )


def _public_configuration(
    configuration: StorageConfiguration,
) -> StorageConfigurationRead:
    return StorageConfigurationRead(
        provider=configuration.provider,
        s3_endpoint_url=configuration.s3_endpoint_url,
        aws_region=configuration.aws_region,
        aws_bucket=configuration.aws_bucket,
        aws_access_key_id=configuration.aws_access_key_id,
        aws_secret_access_key_configured=bool(
            configuration.encrypted_aws_secret_access_key
        ),
    )


@router.get("/configuration", response_model=StorageConfigurationRead)
def read_storage_configuration(
    response: Response,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> StorageConfigurationRead:
    response.headers["Cache-Control"] = "no-store"
    _require_system_superuser(current_user)
    configuration = storage_configuration_service.get(db)
    return _public_configuration(configuration)


@router.get("/objects", response_model=S3ObjectPage)
def list_s3_objects(
    response: Response,
    prefix: str = Query(default="", max_length=1024),
    continuation_token: str | None = Query(default=None, max_length=4096),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> S3ObjectPage:
    response.headers["Cache-Control"] = "no-store"
    _require_system_superuser(current_user)

    try:
        storage = cast(
            S3Storage,
            get_storage_for_provider(StorageProvider.AWS_S3.value, session=db),
        )
        page = storage.list_objects_page(
            prefix=prefix,
            continuation_token=continuation_token,
            max_keys=S3_EXPLORER_PAGE_SIZE,
        )
    except (BotoCoreError, ClientError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Configured S3 storage could not be listed.",
        ) from None
    except (
        StorageConfigurationSecretStorageError,
        StorageConfigurationValidationError,
        RuntimeError,
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configured S3 storage is unavailable.",
        ) from None

    return S3ObjectPage.model_validate(page)


@router.put("/configuration", response_model=StorageConfigurationRead)
def update_storage_configuration(
    payload: StorageConfigurationUpdate,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> StorageConfigurationRead:
    response.headers["Cache-Control"] = "no-store"
    _require_system_superuser(current_user)
    security.validate_request_origin(request)

    try:
        configuration = storage_configuration_service.update(db, payload)
    except StorageConfigurationValidationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except StorageConfigurationSecretStorageError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return _public_configuration(configuration)
