# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Pure local-auth tests that do not require PostgreSQL or MinIO."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from jose import jwt

from app.core import security
from app.core.config import Settings, settings
from app.services import auth


SAFE_TEST_SECRET = "unit-test-auth-secret-0123456789-abcdef"


def _access_claims() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "sub": "42",
        "type": "access",
        "iss": settings.AUTH_JWT_ISSUER,
        "aud": settings.AUTH_JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=15),
    }


def _encode_access_claims(claims: dict[str, object], *, algorithm: str = "HS256") -> str:
    return jwt.encode(
        claims,
        settings.AUTH_JWT_SECRET,
        algorithm=algorithm,
    )


def test_passwords_are_hashed_and_verified_without_plaintext_storage():
    password = "correct horse battery staple"
    password_hash = auth.hash_password(password)

    assert password_hash != password
    assert password_hash.startswith("$bcrypt-sha256$")
    assert auth.verify_password(password, password_hash)
    assert not auth.verify_password("wrong password", password_hash)


def test_email_normalization_is_case_insensitive():
    assert auth.normalize_email("  User@Example.COM ") == "user@example.com"


def test_access_token_contains_local_claims_and_verifies():
    token, expires_in = security.create_access_token(42)

    assert expires_in == settings.AUTH_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    payload = security.verify_access_token(token)
    assert payload["sub"] == "42"
    assert payload["type"] == "access"
    assert payload["iss"] == settings.AUTH_JWT_ISSUER
    assert payload["aud"] == settings.AUTH_JWT_AUDIENCE


def test_tampered_access_token_is_rejected():
    token, _ = security.create_access_token(42)

    with pytest.raises(HTTPException) as exc_info:
        security.verify_access_token(f"{token}tampered")

    assert getattr(exc_info.value, "status_code", None) == 401


@pytest.mark.parametrize("claim", ["exp", "aud", "iat", "iss", "sub", "type"])
def test_required_access_claims_are_rejected_when_missing(claim: str):
    claims = _access_claims()
    claims.pop(claim)

    with pytest.raises(HTTPException) as exc_info:
        security.verify_access_token(_encode_access_claims(claims))

    assert getattr(exc_info.value, "status_code", None) == 401


def test_wrong_access_token_type_is_rejected():
    claims = _access_claims()
    claims["type"] = "refresh"

    with pytest.raises(HTTPException) as exc_info:
        security.verify_access_token(_encode_access_claims(claims))

    assert getattr(exc_info.value, "status_code", None) == 401


def test_non_hs256_access_token_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        security.verify_access_token(_encode_access_claims(_access_claims(), algorithm="HS384"))

    assert getattr(exc_info.value, "status_code", None) == 401


def test_expired_access_token_is_rejected():
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "42",
            "type": "access",
            "iss": settings.AUTH_JWT_ISSUER,
            "aud": settings.AUTH_JWT_AUDIENCE,
            "iat": now - timedelta(minutes=2),
            "exp": now - timedelta(minutes=1),
        },
        settings.AUTH_JWT_SECRET,
        algorithm=settings.AUTH_JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as exc_info:
        security.verify_access_token(token)

    assert getattr(exc_info.value, "status_code", None) == 401


@pytest.mark.parametrize(
    "secret",
    ["", "local-development-only-change-me", "replace-me-with-a-strong-random-secret"],
)
def test_auth_secret_rejects_known_placeholder_and_empty_values(secret: str):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, AUTH_JWT_SECRET=secret)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, AUTH_JWT_SECRET="short-secret")


def test_auth_jwt_secret_has_no_runtime_default():
    assert Settings.model_fields["AUTH_JWT_SECRET"].is_required()


def test_local_cookie_defaults_allow_explicit_secret_without_secure_transport():
    local_settings = Settings(
        _env_file=None,
        AUTH_JWT_SECRET=SAFE_TEST_SECRET,
        AUTH_ENVIRONMENT="development",
        AUTH_COOKIE_SECURE=False,
        AUTH_COOKIE_SAMESITE="lax",
    )

    assert local_settings.AUTH_COOKIE_SECURE is False


def test_samesite_none_requires_secure_cookie():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            AUTH_JWT_SECRET=SAFE_TEST_SECRET,
            AUTH_COOKIE_SAMESITE="none",
            AUTH_COOKIE_SECURE=False,
        )


def test_production_requires_secure_cookie():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            AUTH_JWT_SECRET=SAFE_TEST_SECRET,
            AUTH_ENVIRONMENT="production",
            AUTH_COOKIE_SECURE=False,
        )
