# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Persistence and encryption boundary for global object-storage settings."""

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from sqlmodel import Session

from app.core.config import settings
from app.models.storage_configuration import (
    StorageConfiguration,
    StorageConfigurationUpdate,
    StorageProvider,
)
from app.services.integration import IntegrationService


class StorageConfigurationValidationError(ValueError):
    """A requested storage configuration is incomplete or invalid."""


class StorageConfigurationSecretStorageError(RuntimeError):
    """The S3 secret could not be safely encrypted or decrypted."""


@dataclass(frozen=True)
class StorageRuntimeConfiguration:
    provider: StorageProvider
    s3_endpoint_url: str | None = None
    aws_region: str | None = None
    aws_bucket: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None


class StorageConfigurationService:
    """Read and update the singleton configuration without exposing its secret."""

    def get(self, session: Session) -> StorageConfiguration:
        configuration = session.get(StorageConfiguration, 1)
        if configuration is not None:
            return configuration
        return StorageConfiguration(id=1, provider=StorageProvider.MINIO)

    def update(
        self,
        session: Session,
        payload: StorageConfigurationUpdate,
    ) -> StorageConfiguration:
        existing = session.get(StorageConfiguration, 1)
        configuration = existing or StorageConfiguration(id=1)
        fields_set = payload.model_fields_set
        endpoint_was_supplied = "s3_endpoint_url" in fields_set
        validated_endpoint = (
            self._validate_s3_endpoint_url(payload.s3_endpoint_url)
            if endpoint_was_supplied
            else None
        )

        configuration.provider = payload.provider
        for field_name in ("aws_region", "aws_bucket", "aws_access_key_id"):
            if field_name in fields_set:
                setattr(configuration, field_name, getattr(payload, field_name))
        if endpoint_was_supplied:
            configuration.s3_endpoint_url = validated_endpoint

        supplied_secret = payload.aws_secret_access_key
        if supplied_secret is not None:
            secret_value = supplied_secret.get_secret_value()
            if secret_value:
                configuration.encrypted_aws_secret_access_key = self._encrypt_secret(
                    secret_value
                )

        configuration.updated_at = datetime.utcnow()

        if payload.provider == StorageProvider.AWS_S3:
            missing = [
                label
                for label, value in (
                    ("AWS region", configuration.aws_region),
                    ("AWS bucket", configuration.aws_bucket),
                    ("AWS access key ID", configuration.aws_access_key_id),
                    ("AWS secret access key", configuration.encrypted_aws_secret_access_key),
                )
                if not value
            ]
            if missing:
                raise StorageConfigurationValidationError(
                    f"{', '.join(missing)} required when selecting AWS S3."
                )

        session.add(configuration)
        session.commit()
        session.refresh(configuration)
        return configuration

    def get_runtime_configuration(
        self,
        session: Session,
        *,
        provider: StorageProvider | None = None,
    ) -> StorageRuntimeConfiguration:
        configuration = self.get(session)
        requested_provider = provider or configuration.provider
        if requested_provider == StorageProvider.MINIO:
            return StorageRuntimeConfiguration(provider=StorageProvider.MINIO)
        if requested_provider != StorageProvider.AWS_S3:
            raise StorageConfigurationValidationError(
                f"Unsupported storage provider: {requested_provider}."
            )

        if not all(
            (
                configuration.aws_region,
                configuration.aws_bucket,
                configuration.aws_access_key_id,
                configuration.encrypted_aws_secret_access_key,
            )
        ):
            raise StorageConfigurationValidationError(
                "AWS S3 configuration is incomplete."
            )
        try:
            secret = self._encryption_service().decrypt_token(
                configuration.encrypted_aws_secret_access_key
            )
        except (InvalidToken, TypeError, ValueError):
            raise StorageConfigurationSecretStorageError(
                "AWS secret access key cannot be decrypted; verify TOKEN_ENCRYPTION_KEY."
            ) from None

        return StorageRuntimeConfiguration(
            provider=requested_provider,
            s3_endpoint_url=configuration.s3_endpoint_url,
            aws_region=configuration.aws_region,
            aws_bucket=configuration.aws_bucket,
            aws_access_key_id=configuration.aws_access_key_id,
            aws_secret_access_key=secret,
        )

    @staticmethod
    def _validate_s3_endpoint_url(value: str | None) -> str | None:
        if value is None or not value.strip():
            return None

        error_message = (
            "S3 endpoint URL must be an absolute HTTPS URL without credentials, query, or fragment."
        )
        normalized = value.strip()
        if len(normalized) > 2048 or any(character.isspace() for character in normalized):
            raise StorageConfigurationValidationError(error_message)
        try:
            parsed = urlsplit(normalized)
            _ = parsed.port
        except ValueError:
            raise StorageConfigurationValidationError(error_message) from None
        if (
            parsed.scheme.lower() != "https"
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in normalized
            or "#" in normalized
            or "\\" in normalized
        ):
            raise StorageConfigurationValidationError(error_message)
        return normalized.rstrip("/")

    @staticmethod
    def _encryption_service() -> IntegrationService:
        return IntegrationService(encryption_key=settings.TOKEN_ENCRYPTION_KEY)

    def _encrypt_secret(self, secret: str) -> str:
        try:
            return self._encryption_service().encrypt_token(secret)
        except (TypeError, ValueError):
            # Never include the supplied secret in the storage failure or its logs.
            raise StorageConfigurationSecretStorageError(
                "AWS secret storage is unavailable; configure TOKEN_ENCRYPTION_KEY."
            ) from None


storage_configuration_service = StorageConfigurationService()
