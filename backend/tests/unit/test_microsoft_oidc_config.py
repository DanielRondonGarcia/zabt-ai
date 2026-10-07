# SPDX-License-Identifier: AGPL-3.0-only
"""Global public-client Microsoft OIDC configuration policy tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.microsoft_oidc_configuration import (
    MicrosoftOidcConfigurationValidationError,
    validate_microsoft_oidc_client_id,
    validate_microsoft_oidc_redirect_uri,
    validate_microsoft_oidc_tenant,
)


AUTH_SECRET = "config-test-auth-secret-with-enough-entropy-12345!"
CLIENT_ID = "11111111-1111-4111-8111-111111111111"
TENANT_GUID = "22222222-2222-4222-8222-222222222222"


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, AUTH_JWT_SECRET=AUTH_SECRET, **overrides)


@pytest.mark.parametrize("tenant", ["common", "organizations", "consumers", TENANT_GUID])
def test_public_oidc_tenant_policy_allows_aliases_and_guid(tenant: str):
    assert validate_microsoft_oidc_tenant(tenant) == tenant


@pytest.mark.parametrize("tenant", ["contoso.onmicrosoft.com", "tenant.example.com", "tenant-id", ""])
def test_public_oidc_tenant_policy_rejects_unresolved_names(tenant: str):
    with pytest.raises(MicrosoftOidcConfigurationValidationError):
        validate_microsoft_oidc_tenant(tenant)


@pytest.mark.parametrize("client_id", [CLIENT_ID, CLIENT_ID.upper()])
def test_client_id_must_be_a_guid(client_id: str):
    assert validate_microsoft_oidc_client_id(client_id) == CLIENT_ID


@pytest.mark.parametrize("client_id", ["client-id", "", "{11111111-1111-4111-8111-111111111111}"])
def test_invalid_client_id_is_rejected(client_id: str):
    with pytest.raises(MicrosoftOidcConfigurationValidationError):
        validate_microsoft_oidc_client_id(client_id)


def test_nonproduction_allows_local_http_spa_redirect(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.services.microsoft_oidc_configuration.settings.AUTH_ENVIRONMENT", "development")

    assert (
        validate_microsoft_oidc_redirect_uri(
            "http://localhost:3001/login"
        )
        == "http://localhost:3001/login"
    )


def test_redirect_uri_must_match_the_current_spa_origin(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "app.services.microsoft_oidc_configuration.settings.AUTH_ENVIRONMENT",
        "development",
    )

    assert (
        validate_microsoft_oidc_redirect_uri(
            "http://localhost:3001/login",
            spa_origin="http://localhost:3001",
        )
        == "http://localhost:3001/login"
    )
    with pytest.raises(MicrosoftOidcConfigurationValidationError, match="current SPA origin"):
        validate_microsoft_oidc_redirect_uri(
            "http://localhost:8000/login",
            spa_origin="http://localhost:3001",
        )


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "",
        "not-a-url",
        "http://localhost:3001/login?code=unsafe",
        "https://user:password@example.com/callback",
    ],
)
def test_redirect_uri_rejects_unsafe_values(redirect_uri: str):
    with pytest.raises(MicrosoftOidcConfigurationValidationError):
        validate_microsoft_oidc_redirect_uri(redirect_uri)


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost:3001/login",
        "https://localhost/login",
        "https://127.0.0.1/login",
        "https://app.example.com/login",
    ],
)
def test_production_redirect_policy_requires_public_https(
    monkeypatch: pytest.MonkeyPatch,
    redirect_uri: str,
):
    monkeypatch.setattr("app.services.microsoft_oidc_configuration.settings.AUTH_ENVIRONMENT", "production")
    if redirect_uri.startswith("https://app.example.com"):
        assert validate_microsoft_oidc_redirect_uri(redirect_uri) == redirect_uri
    else:
        with pytest.raises(MicrosoftOidcConfigurationValidationError):
            validate_microsoft_oidc_redirect_uri(redirect_uri)


def test_production_settings_boot_without_oidc_environment_values():
    configured = make_settings(
        AUTH_ENVIRONMENT="production",
        AUTH_COOKIE_SECURE=True,
        MICROSOFT_CLIENT_ID="",
        MICROSOFT_CLIENT_SECRET="",
        MICROSOFT_OIDC_REDIRECT_URI="",
    )

    assert configured.MICROSOFT_OIDC_REDIRECT_URI == ""
