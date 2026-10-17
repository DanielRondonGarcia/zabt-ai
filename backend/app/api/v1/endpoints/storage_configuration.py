# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""System-superuser API for global object-storage settings."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session

from app.api.deps import get_current_active_user, get_db
from app.core import security
from app.models import User
from app.models.storage_configuration import (
    StorageConfiguration,
    StorageConfigurationRead,
    StorageConfigurationUpdate,
)
from app.services.storage_configuration import (
    StorageConfigurationSecretStorageError,
    StorageConfigurationValidationError,
    storage_configuration_service,
)

router = APIRouter()


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
