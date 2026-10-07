# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Microsoft OIDC tests with discovery, token, and JWKS HTTP fully mocked."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.services import microsoft_oidc as oidc_module
from app.services.microsoft_oidc import (
    MICROSOFT_AUTH_BASE,
    MicrosoftOidcClient,
    MicrosoftOidcProviderError,
    MicrosoftOidcValidationError,
    identity_from_claims,
    is_microsoft_oidc_configured,
)
from app.services.oauth_state import build_pkce_challenge


TENANT_GUID = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT_GUID = "22222222-2222-4222-8222-222222222222"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("invalid json")
        return self._payload


class FakeAsyncClient:
    responses: list[FakeResponse] = []
    calls: list[tuple[str, str, dict]] = []

    def __init__(self, *, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, {"timeout": self.timeout, **kwargs}))
        return self.responses.pop(0)

    async def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, {"timeout": self.timeout, **kwargs}))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch: pytest.MonkeyPatch):
    FakeAsyncClient.responses = []
    FakeAsyncClient.calls = []
    monkeypatch.setattr(oidc_module.httpx, "AsyncClient", FakeAsyncClient)


@pytest.fixture
def client() -> MicrosoftOidcClient:
    return MicrosoftOidcClient(
        client_id="client-id",
        client_secret="client-secret",
        tenant_id="common",
        redirect_uri="https://app.example/auth/microsoft/callback",
        http_timeout=7.5,
    )


@pytest.fixture
def signing_material():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_numbers = private_key.public_key().public_numbers()

    def encode(number: int) -> str:
        raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "kid-1",
        "n": encode(public_numbers.n),
        "e": encode(public_numbers.e),
    }
    return private_pem, jwk


def discovery_payload(
    *,
    tenant: str = "common",
    issuer: str | None = None,
) -> dict[str, str]:
    return {
        "authorization_endpoint": f"{MICROSOFT_AUTH_BASE}/{tenant}/oauth2/v2.0/authorize",
        "token_endpoint": f"{MICROSOFT_AUTH_BASE}/{tenant}/oauth2/v2.0/token",
        "jwks_uri": f"{MICROSOFT_AUTH_BASE}/{tenant}/discovery/v2.0/keys",
        "issuer": issuer or f"{MICROSOFT_AUTH_BASE}/{{tenantid}}/v2.0",
    }


def make_id_token(private_pem: bytes, *, nonce: str, **overrides) -> str:
    claims = {
        "iss": f"{MICROSOFT_AUTH_BASE}/{TENANT_GUID}/v2.0",
        "aud": "client-id",
        "sub": "subject-1",
        "tid": TENANT_GUID,
        "nonce": nonce,
        "name": "Ada Example",
        "preferred_username": "ADA@EXAMPLE.COM",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": "kid-1"})


@pytest.mark.asyncio
async def test_build_authorization_url_fetches_discovery_and_uses_pkce(client: MicrosoftOidcClient):
    FakeAsyncClient.responses = [FakeResponse(200, discovery_payload())]
    verifier = "v" * 43

    url = await client.build_authorization_url(
        state="state-value",
        nonce="nonce-value",
        code_challenge=build_pkce_challenge(verifier),
    )

    params = parse_qs(urlparse(url).query)
    assert url.startswith(f"{MICROSOFT_AUTH_BASE}/common/oauth2/v2.0/authorize?")
    assert params["client_id"] == ["client-id"]
    assert params["scope"] == ["openid profile email"]
    assert params["state"] == ["state-value"]
    assert params["nonce"] == ["nonce-value"]
    assert params["code_challenge"] == [build_pkce_challenge(verifier)]
    assert params["code_challenge_method"] == ["S256"]
    assert FakeAsyncClient.calls[0][2]["timeout"] == 7.5


@pytest.mark.asyncio
async def test_exchange_code_uses_code_verifier_and_does_not_need_graph_access(client: MicrosoftOidcClient):
    FakeAsyncClient.responses = [
        FakeResponse(200, discovery_payload()),
        FakeResponse(200, {"id_token": "signed-id-token", "access_token": "ignored"}),
    ]

    token_data = await client.exchange_code(code="authorization-code", code_verifier="v" * 43)

    assert token_data["id_token"] == "signed-id-token"
    method, url, kwargs = FakeAsyncClient.calls[1]
    assert method == "POST"
    assert url == f"{MICROSOFT_AUTH_BASE}/common/oauth2/v2.0/token"
    assert kwargs["data"]["code"] == "authorization-code"
    assert kwargs["data"]["code_verifier"] == "v" * 43
    assert kwargs["data"]["client_secret"] == "client-secret"


@pytest.mark.asyncio
async def test_discovery_rejects_arbitrary_token_endpoint_before_secret_bearing_request(
    client: MicrosoftOidcClient,
):
    hostile = discovery_payload()
    hostile["token_endpoint"] = "https://evil.example/token"
    FakeAsyncClient.responses = [FakeResponse(200, hostile)]

    with pytest.raises(MicrosoftOidcProviderError):
        await client.exchange_code(code="authorization-code", code_verifier="v" * 43)

    assert [call[0] for call in FakeAsyncClient.calls] == ["GET"]
    assert all("client-secret" not in repr(call) for call in FakeAsyncClient.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [
        ("authorization_endpoint", "https://evil.example/authorize"),
        ("issuer", "https://evil.example/tenant/v2.0"),
        ("jwks_uri", "https://evil.example/keys"),
        ("jwks_uri", f"{MICROSOFT_AUTH_BASE}:443/common/discovery/v2.0/keys"),
    ],
)
async def test_discovery_rejects_forged_host_path_or_issuer_metadata(
    client: MicrosoftOidcClient,
    field: str,
    value: str,
):
    hostile = discovery_payload()
    hostile[field] = value
    FakeAsyncClient.responses = [FakeResponse(200, hostile)]

    with pytest.raises(MicrosoftOidcProviderError):
        await client.discovery()

    assert len(FakeAsyncClient.calls) == 1
    assert FakeAsyncClient.calls[0][1] == client.discovery_url


@pytest.mark.asyncio
async def test_validate_id_token_checks_jwks_signature_issuer_audience_exp_and_nonce(
    client: MicrosoftOidcClient,
    signing_material,
):
    private_pem, jwk = signing_material
    nonce = "nonce-value"
    jwk["issuer"] = f"{MICROSOFT_AUTH_BASE}/{{tenantid}}/v2.0"
    FakeAsyncClient.responses = [
        FakeResponse(200, discovery_payload()),
        FakeResponse(200, {"keys": [jwk]}),
    ]

    claims = await client.validate_id_token(make_id_token(private_pem, nonce=nonce), nonce=nonce)

    assert claims["sub"] == "subject-1"
    assert claims["tid"] == TENANT_GUID
    assert [call[0] for call in FakeAsyncClient.calls] == ["GET", "GET"]
    assert FakeAsyncClient.calls[1][1] == f"{MICROSOFT_AUTH_BASE}/common/discovery/v2.0/keys"


@pytest.mark.asyncio
async def test_validate_id_token_rejects_signing_jwk_issuer_mismatch(
    client: MicrosoftOidcClient,
    signing_material,
):
    private_pem, jwk = signing_material
    jwk["issuer"] = "https://login.microsoftonline.com/other-tenant/v2.0"
    FakeAsyncClient.responses = [
        FakeResponse(200, discovery_payload()),
        FakeResponse(200, {"keys": [jwk]}),
    ]

    with pytest.raises(MicrosoftOidcValidationError):
        await client.validate_id_token(
            make_id_token(private_pem, nonce="nonce-value"),
            nonce="nonce-value",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides, expected_nonce",
    [
        ({"aud": "other-client"}, "nonce-value"),
        ({"exp": datetime.now(timezone.utc) - timedelta(minutes=1)}, "nonce-value"),
        ({}, "wrong-nonce"),
    ],
)
async def test_validate_id_token_rejects_invalid_claims(
    client: MicrosoftOidcClient,
    signing_material,
    overrides,
    expected_nonce,
):
    private_pem, jwk = signing_material
    FakeAsyncClient.responses = [
        FakeResponse(200, discovery_payload()),
        FakeResponse(200, {"keys": [jwk]}),
    ]

    with pytest.raises(MicrosoftOidcValidationError):
        await client.validate_id_token(
            make_id_token(private_pem, nonce="nonce-value", **overrides),
            nonce=expected_nonce,
        )


def test_identity_from_validated_claims_normalizes_email_and_ignores_invalid_email():
    identity = identity_from_claims(
        {
            "sub": "subject-1",
            "tid": "tenant-1",
            "email": "  User@Example.COM ",
            "name": "Ada Example",
            "picture": "https://images.example/avatar.png",
        }
    )
    assert identity.email == "user@example.com"
    assert identity.full_name == "Ada Example"

    invalid = identity_from_claims(
        {"sub": "subject-2", "tid": "tenant-1", "email": "not-an-email"}
    )
    assert invalid.email is None


def test_oidc_configuration_fails_closed_when_credentials_have_no_redirect_uri(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(oidc_module.settings, "MICROSOFT_CLIENT_ID", "client-id")
    monkeypatch.setattr(oidc_module.settings, "MICROSOFT_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(oidc_module.settings, "MICROSOFT_TENANT_ID", "common")
    monkeypatch.setattr(oidc_module.settings, "MICROSOFT_OIDC_REDIRECT_URI", "")

    assert is_microsoft_oidc_configured() is False


@pytest.mark.asyncio
async def test_common_requires_guid_tenant_and_concrete_tenant_requires_exact_tid(
    signing_material,
):
    private_pem, jwk = signing_material
    common_client = MicrosoftOidcClient(
        client_id="client-id",
        client_secret="client-secret",
        tenant_id="common",
        redirect_uri="https://app.example/auth/microsoft/callback",
    )
    FakeAsyncClient.responses = [
        FakeResponse(200, discovery_payload()),
        FakeResponse(200, {"keys": [jwk]}),
    ]
    with pytest.raises(MicrosoftOidcValidationError):
        await common_client.validate_id_token(
            make_id_token(private_pem, nonce="nonce-value", tid="not-a-guid"),
            nonce="nonce-value",
        )
    assert FakeAsyncClient.calls == []

    concrete_client = MicrosoftOidcClient(
        client_id="client-id",
        client_secret="client-secret",
        tenant_id=TENANT_GUID,
        redirect_uri="https://app.example/auth/microsoft/callback",
    )
    FakeAsyncClient.responses = []
    with pytest.raises(MicrosoftOidcValidationError):
        await concrete_client.validate_id_token(
            make_id_token(private_pem, nonce="nonce-value", tid=OTHER_TENANT_GUID),
            nonce="nonce-value",
        )
    assert FakeAsyncClient.calls == []
