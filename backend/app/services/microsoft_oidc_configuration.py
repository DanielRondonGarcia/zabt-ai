# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Validation and persistence helpers for global Microsoft OIDC settings."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlmodel import Session, select

from app.core.config import settings, validate_microsoft_tenant_id
from app.models import MicrosoftOidcConfiguration, MicrosoftOidcProvider


MICROSOFT_OIDC_CLIENT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
MAX_MICROSOFT_OIDC_REDIRECT_URI_LENGTH = 2048
MICROSOFT_OIDC_REDIRECT_SCHEMES = frozenset({"http", "https"})
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class MicrosoftOidcConfigurationValidationError(ValueError):
    """Raised when an administrator submits an unsafe public OIDC setting."""


def validate_microsoft_oidc_client_id(value: Any) -> str:
    """Require the canonical GUID form used by Microsoft app registrations."""

    if not isinstance(value, str):
        raise MicrosoftOidcConfigurationValidationError("Client ID must be a GUID")
    client_id = value.strip()
    if client_id != value or not MICROSOFT_OIDC_CLIENT_ID_RE.fullmatch(client_id):
        raise MicrosoftOidcConfigurationValidationError("Client ID must be a GUID")
    try:
        return str(UUID(client_id))
    except ValueError as exc:
        raise MicrosoftOidcConfigurationValidationError("Client ID must be a GUID") from exc


def validate_microsoft_oidc_tenant(value: Any) -> str:
    """Allow a tenant GUID or one of Microsoft's documented authority aliases."""

    if not isinstance(value, str) or value.strip() != value:
        raise MicrosoftOidcConfigurationValidationError(
            "Tenant must be a tenant GUID, common, organizations, or consumers"
        )
    try:
        return validate_microsoft_tenant_id(value)
    except ValueError as exc:
        raise MicrosoftOidcConfigurationValidationError(
            "Tenant must be a tenant GUID, common, organizations, or consumers"
        ) from exc


def validate_microsoft_oidc_redirect_uri(
    value: Any,
    *,
    spa_origin: str | None = None,
) -> str:
    """Validate an exact same-origin SPA redirect URI."""

    if not isinstance(value, str):
        raise MicrosoftOidcConfigurationValidationError("Redirect URI is invalid")
    redirect_uri = value.strip()
    if (
        redirect_uri != value
        or not redirect_uri
        or len(redirect_uri) > MAX_MICROSOFT_OIDC_REDIRECT_URI_LENGTH
        or any(character.isspace() for character in redirect_uri)
    ):
        raise MicrosoftOidcConfigurationValidationError("Redirect URI is invalid")

    try:
        parsed = urlsplit(redirect_uri)
        port = parsed.port
    except ValueError as exc:
        raise MicrosoftOidcConfigurationValidationError("Redirect URI is invalid") from exc

    hostname = parsed.hostname.casefold() if parsed.hostname else None
    if (
        parsed.scheme.casefold() not in MICROSOFT_OIDC_REDIRECT_SCHEMES
        or not parsed.netloc
        or hostname is None
        or parsed.path != "/login"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is None
        and parsed.netloc.endswith(":")
    ):
        raise MicrosoftOidcConfigurationValidationError("Redirect URI is invalid")

    if spa_origin is not None:
        expected_origin = spa_origin.strip().rstrip("/")
        try:
            expected = urlsplit(expected_origin)
        except ValueError as exc:
            raise MicrosoftOidcConfigurationValidationError(
                "Redirect URI must use the current SPA origin"
            ) from exc
        if (
            expected.scheme.casefold() not in MICROSOFT_OIDC_REDIRECT_SCHEMES
            or not expected.netloc
            or expected.path
            or expected.query
            or expected.fragment
            or expected.username is not None
            or expected.password is not None
            or f"{parsed.scheme}://{parsed.netloc}".casefold()
            != expected_origin.casefold()
        ):
            raise MicrosoftOidcConfigurationValidationError(
                "Redirect URI must use the current SPA origin"
            )

    if settings.AUTH_ENVIRONMENT == "production":
        if parsed.scheme.casefold() != "https" or hostname in _LOCAL_HOSTS:
            raise MicrosoftOidcConfigurationValidationError(
                "Production redirect URI must use public HTTPS"
            )

    return redirect_uri


def get_microsoft_oidc_configuration(
    db: Session,
) -> MicrosoftOidcConfiguration | None:
    """Return the singleton Microsoft configuration, if one has been saved."""

    return db.exec(
        select(MicrosoftOidcConfiguration).where(
            MicrosoftOidcConfiguration.provider == MicrosoftOidcProvider.MICROSOFT
        )
    ).first()


def is_microsoft_oidc_runtime_configured(
    configuration: MicrosoftOidcConfiguration | None,
) -> bool:
    """Return whether the stored configuration can be used for browser sign-in."""

    if not configuration or not configuration.enabled:
        return False
    try:
        validate_microsoft_oidc_client_id(configuration.client_id)
        validate_microsoft_oidc_tenant(configuration.tenant_id)
        validate_microsoft_oidc_redirect_uri(configuration.redirect_uri)
    except MicrosoftOidcConfigurationValidationError:
        return False
    return True


def upsert_microsoft_oidc_configuration(
    db: Session,
    *,
    client_id: Any,
    tenant: Any,
    redirect_uri: Any,
    enabled: bool,
    updated_by: int,
    spa_origin: str | None = None,
) -> MicrosoftOidcConfiguration:
    """Validate and upsert the one global public-client configuration."""

    normalized_client_id = validate_microsoft_oidc_client_id(client_id)
    normalized_tenant = validate_microsoft_oidc_tenant(tenant)
    normalized_redirect_uri = validate_microsoft_oidc_redirect_uri(
        redirect_uri,
        spa_origin=spa_origin,
    )

    configuration = get_microsoft_oidc_configuration(db)
    now = datetime.utcnow()
    if configuration is None:
        configuration = MicrosoftOidcConfiguration(
            provider=MicrosoftOidcProvider.MICROSOFT,
            client_id=normalized_client_id,
            tenant_id=normalized_tenant,
            redirect_uri=normalized_redirect_uri,
            enabled=bool(enabled),
            created_at=now,
            updated_at=now,
            updated_by=updated_by,
        )
    else:
        configuration.client_id = normalized_client_id
        configuration.tenant_id = normalized_tenant
        configuration.redirect_uri = normalized_redirect_uri
        configuration.enabled = bool(enabled)
        configuration.updated_at = now
        configuration.updated_by = updated_by

    db.add(configuration)
    db.flush()
    return configuration
