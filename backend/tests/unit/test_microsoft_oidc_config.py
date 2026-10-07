# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Configuration policy tests for Microsoft OIDC deployment settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


AUTH_SECRET = "config-test-auth-secret-with-enough-entropy-12345!"
TENANT_GUID = "11111111-1111-4111-8111-111111111111"


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, AUTH_JWT_SECRET=AUTH_SECRET, **overrides)


@pytest.mark.parametrize(
    "tenant_id",
    ["contoso.onmicrosoft.com", "tenant.example.com", "tenant-id", ""],
)
def test_concrete_tenant_domains_and_unresolved_names_are_rejected(tenant_id: str):
    with pytest.raises(ValidationError, match="MICROSOFT_TENANT_ID"):
        make_settings(MICROSOFT_TENANT_ID=tenant_id)


@pytest.mark.parametrize("tenant_id", ["common", "organizations", "consumers", TENANT_GUID])
def test_microsoft_tenant_policy_allows_documented_aliases_and_guid(tenant_id: str):
    configured = make_settings(MICROSOFT_TENANT_ID=tenant_id)

    assert configured.MICROSOFT_TENANT_ID == tenant_id


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "",
        "http://api.example.com/api/v1/auth/microsoft/callback",
        "https://localhost/api/v1/auth/microsoft/callback",
        "https://127.0.0.1/api/v1/auth/microsoft/callback",
        "https://[::1]/api/v1/auth/microsoft/callback",
    ],
)
def test_production_requires_public_https_oidc_redirect_uri(redirect_uri: str):
    with pytest.raises(ValidationError, match="MICROSOFT_OIDC_REDIRECT_URI"):
        make_settings(
            AUTH_ENVIRONMENT="production",
            AUTH_COOKIE_SECURE=True,
            MICROSOFT_OIDC_REDIRECT_URI=redirect_uri,
        )


def test_production_accepts_public_https_oidc_redirect_uri():
    configured = make_settings(
        AUTH_ENVIRONMENT="production",
        AUTH_COOKIE_SECURE=True,
        MICROSOFT_OIDC_REDIRECT_URI="https://api.example.com/api/v1/auth/microsoft/callback",
    )

    assert configured.MICROSOFT_OIDC_REDIRECT_URI.startswith("https://")


def test_development_allows_empty_oidc_redirect_for_optional_local_sign_in():
    configured = make_settings(
        AUTH_ENVIRONMENT="development",
        MICROSOFT_OIDC_REDIRECT_URI="",
    )

    assert configured.MICROSOFT_OIDC_REDIRECT_URI == ""
