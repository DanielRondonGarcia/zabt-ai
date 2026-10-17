# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Service contracts for write-only global storage credentials."""

from collections.abc import Generator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine


@pytest.fixture()
def storage_service_db(monkeypatch: pytest.MonkeyPatch) -> Generator:
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embedding-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    from app.core.config import settings
    from app.models.storage_configuration import StorageConfiguration
    from app.services.storage_configuration import StorageConfigurationService

    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    StorageConfiguration.__table__.create(db_engine)
    yield StorageConfigurationService(), db_engine
    db_engine.dispose()


def test_explicitly_empty_secret_preserves_the_existing_secret(storage_service_db):
    service, db_engine = storage_service_db
    from app.models.storage_configuration import (
        StorageConfigurationUpdate,
        StorageProvider,
    )

    with Session(db_engine) as session:
        service.update(
            session,
            StorageConfigurationUpdate(
                provider=StorageProvider.AWS_S3,
                aws_region="us-east-1",
                aws_bucket="zabt-recordings",
                aws_access_key_id="AKIAEXAMPLE",
                aws_secret_access_key="initial-secret",
            ),
        )
        service.update(
            session,
            StorageConfigurationUpdate(
                provider=StorageProvider.AWS_S3,
                aws_secret_access_key="",
            ),
        )
        service.update(
            session,
            StorageConfigurationUpdate(
                provider=StorageProvider.AWS_S3,
                aws_secret_access_key="   ",
            ),
        )
        runtime_configuration = service.get_runtime_configuration(session)

    assert runtime_configuration.provider == StorageProvider.AWS_S3
    assert runtime_configuration.aws_secret_access_key == "initial-secret"


def test_empty_secret_cannot_enable_s3_without_a_stored_secret(storage_service_db):
    service, db_engine = storage_service_db
    from app.models.storage_configuration import (
        StorageConfigurationUpdate,
        StorageProvider,
    )
    from app.services.storage_configuration import StorageConfigurationValidationError

    with Session(db_engine) as session:
        with pytest.raises(
            StorageConfigurationValidationError,
            match="AWS secret access key required when selecting AWS S3\\.",
        ):
            service.update(
                session,
                StorageConfigurationUpdate(
                    provider=StorageProvider.AWS_S3,
                    aws_region="us-east-1",
                    aws_bucket="zabt-recordings",
                    aws_access_key_id="AKIAEXAMPLE",
                    aws_secret_access_key="",
                ),
            )
        assert service.get(session).provider == StorageProvider.MINIO
