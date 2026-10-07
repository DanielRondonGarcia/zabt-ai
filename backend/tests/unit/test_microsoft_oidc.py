# SPDX-License-Identifier: AGPL-3.0-only
"""Microsoft public-client OIDC verification tests with all HTTP mocked."""

from __future__ import annotations

import base64
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.models import MicrosoftOidcConfiguration
from app.services import microsoft_oidc as oidc_module
from app.services.microsoft_oidc import (
    MICROSOFT_AUTH_BASE,
    MicrosoftOidcClient,
    MicrosoftOidcProviderError,
    MicrosoftOidcValidationError,
    identity_from_claims,
    is_microsoft_oidc_configured,
)


CLIENT_ID = "11111111-1111-4111-8111-111111111111"
TENANT_GUID = "22222222-2222-4222-8222-222222222222"
OTHER_TENANT_GUID = "33333333-3333-4333-8333-333333333333"


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


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch: pytest.MonkeyPatch):
    FakeAsyncClient.responses = []
    FakeAsyncClient.calls = []
    oidc_module.clear_microsoft_oidc_caches()
    monkeypatch.setattr(oidc_module.httpx, "AsyncClient", FakeAsyncClient)


@pytest.fixture
def client() -> MicrosoftOidcClient:
    return MicrosoftOidcClient(
        client_id=CLIENT_ID,
        tenant_id="common",
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


def make_id_token(
    private_pem: bytes,
    *,
    nonce: str = "nonce-value",
    kid: str = "kid-1",
    **overrides,
) -> str:
    claims = {
        "iss": f"{MICROSOFT_AUTH_BASE}/{TENANT_GUID}/v2.0",
        "aud": CLIENT_ID,
        "sub": "subject-1",
        "tid": TENANT_GUID,
        "nonce": nonce,
        "name": "Ada Example",
        "preferred_username": "ADA@EXAMPLE.COM",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


@pytest.mark.asyncio
async def test_discovery_is_pinned_to_microsoft_authority(client: MicrosoftOidcClient):
    FakeAsyncClient.responses = [FakeResponse(200, discovery_payload())]

    metadata = await client.discovery()

    assert metadata["issuer"] == f"{MICROSOFT_AUTH_BASE}/{{tenantid}}/v2.0"
    assert FakeAsyncClient.calls[0][1] == client.discovery_url
    assert FakeAsyncClient.calls[0][2]["timeout"] == 7.5


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_response", [FakeResponse(503), FakeResponse(200, None)])
async def test_failed_discovery_is_negative_cached_and_coalesced(
    client: MicrosoftOidcClient,
    failed_response: FakeResponse,
):
    FakeAsyncClient.responses = [failed_response]

    results = await asyncio.gather(
        client.discovery(),
        client.discovery(),
        return_exceptions=True,
    )

    assert all(isinstance(result, MicrosoftOidcProviderError) for result in results)
    assert len(FakeAsyncClient.calls) == 1

    with pytest.raises(MicrosoftOidcProviderError):
        await client.discovery()
    assert len(FakeAsyncClient.calls) == 1

    oidc_module.clear_microsoft_oidc_caches()
    FakeAsyncClient.responses = [FakeResponse(200, discovery_payload())]
    await client.discovery()
    assert len(FakeAsyncClient.calls) == 2


@pytest.mark.asyncio
async def test_successful_discovery_refresh_clears_negative_cache(
    client: MicrosoftOidcClient,
    monkeypatch: pytest.MonkeyPatch,
):
    FakeAsyncClient.responses = [
        FakeResponse(503),
        FakeResponse(200, discovery_payload()),
    ]

    with pytest.raises(MicrosoftOidcProviderError):
        await client.discovery()
    monkeypatch.setattr(oidc_module, "MICROSOFT_OIDC_FAILURE_COOLDOWN_SECONDS", 0.0)

    await client.discovery()
    await client.discovery()
    assert len(FakeAsyncClient.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_response", [FakeResponse(503), FakeResponse(200, None)])
async def test_failed_jwks_is_negative_cached_for_sequential_and_concurrent_validation(
    client: MicrosoftOidcClient,
    signing_material,
    failed_response: FakeResponse,
):
    private_pem, jwk = signing_material
    FakeAsyncClient.responses = [
        FakeResponse(200, discovery_payload()),
        failed_response,
    ]
    token = make_id_token(private_pem)

    results = await asyncio.gather(
        client.validate_id_token(token),
        client.validate_id_token(token),
        return_exceptions=True,
    )

    assert all(isinstance(result, MicrosoftOidcProviderError) for result in results)
    assert len(FakeAsyncClient.calls) == 2
    with pytest.raises(MicrosoftOidcProviderError):
        await client.validate_id_token(token)
    assert len(FakeAsyncClient.calls) == 2


@pytest.mark.asyncio
async def test_unknown_kid_cannot_bypass_active_jwks_failure_cooldown(
    client: MicrosoftOidcClient,
    signing_material,
):
    private_pem, jwk = signing_material
    jwk["issuer"] = f"{MICROSOFT_AUTH_BASE}/{{tenantid}}/v2.0"
    FakeAsyncClient.responses = [
        FakeResponse(200, discovery_payload()),
        FakeResponse(200, {"keys": [jwk]}),
        FakeResponse(503),
    ]

    await client.validate_id_token(make_id_token(private_pem))
    unknown_kid_token = make_id_token(private_pem, kid="rotated-kid")
    with pytest.raises(MicrosoftOidcProviderError):
        await client.validate_id_token(unknown_kid_token)
    with pytest.raises(MicrosoftOidcProviderError):
        await client.validate_id_token(unknown_kid_token)

    assert len(FakeAsyncClient.calls) == 3


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
async def test_discovery_rejects_forged_metadata_before_jwks_fetch(
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
    jwk["issuer"] = f"{MICROSOFT_AUTH_BASE}/{{tenantid}}/v2.0"
    FakeAsyncClient.responses = [
        FakeResponse(200, discovery_payload()),
        FakeResponse(200, {"keys": [jwk]}),
    ]

    claims = await client.validate_id_token(make_id_token(private_pem))

    assert claims["sub"] == "subject-1"
    assert claims["tid"] == TENANT_GUID
    assert [call[0] for call in FakeAsyncClient.calls] == ["GET", "GET"]
    assert FakeAsyncClient.calls[1][1] == f"{MICROSOFT_AUTH_BASE}/common/discovery/v2.0/keys"


@pytest.mark.asyncio
async def test_repeated_unknown_kids_use_bounded_jwks_refreshes(
    client: MicrosoftOidcClient,
    signing_material,
):
    private_pem, jwk = signing_material
    jwk["issuer"] = f"{MICROSOFT_AUTH_BASE}/{{tenantid}}/v2.0"
    FakeAsyncClient.responses = [
        FakeResponse(200, discovery_payload()),
        FakeResponse(200, {"keys": [jwk]}),
        # One bounded refresh is allowed after the cached key set misses a kid.
        FakeResponse(200, {"keys": [jwk]}),
    ]

    await client.validate_id_token(make_id_token(private_pem))
    invalid_tokens = await asyncio.gather(
        client.validate_id_token(make_id_token(private_pem, kid="fake-kid")),
        client.validate_id_token(make_id_token(private_pem, kid="fake-kid")),
        return_exceptions=True,
    )

    assert all(isinstance(result, MicrosoftOidcValidationError) for result in invalid_tokens)
    # Discovery + initial JWKS + one coalesced/limited unknown-kid refresh.
    assert len(FakeAsyncClient.calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "other-client"},
        {"exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        {"nonce": ""},
    ],
)
async def test_validate_id_token_rejects_invalid_claims(
    client: MicrosoftOidcClient,
    signing_material,
    overrides,
):
    private_pem, jwk = signing_material
    FakeAsyncClient.responses = [
        FakeResponse(200, discovery_payload()),
        FakeResponse(200, {"keys": [jwk]}),
    ]

    with pytest.raises(MicrosoftOidcValidationError):
        await client.validate_id_token(make_id_token(private_pem, **overrides))


@pytest.mark.asyncio
async def test_common_requires_guid_tenant_and_concrete_tenant_requires_exact_tid(
    signing_material,
):
    private_pem, jwk = signing_material
    common_client = MicrosoftOidcClient(client_id=CLIENT_ID, tenant_id="common")
    FakeAsyncClient.responses = []
    with pytest.raises(MicrosoftOidcValidationError):
        await common_client.validate_id_token(
            make_id_token(private_pem, tid="not-a-guid")
        )
    assert FakeAsyncClient.calls == []

    concrete_client = MicrosoftOidcClient(client_id=CLIENT_ID, tenant_id=TENANT_GUID)
    with pytest.raises(MicrosoftOidcValidationError):
        await concrete_client.validate_id_token(
            make_id_token(private_pem, tid=OTHER_TENANT_GUID)
        )
    assert FakeAsyncClient.calls == []


def test_identity_from_validated_claims_normalizes_email_and_ignores_invalid_email():
    identity = identity_from_claims(
        {
            "sub": "subject-1",
            "tid": TENANT_GUID,
            "email": "  User@Example.COM ",
            "name": "Ada Example",
            "picture": "https://images.example/avatar.png",
        }
    )
    assert identity.email == "user@example.com"
    assert identity.full_name == "Ada Example"

    invalid = identity_from_claims(
        {"sub": "subject-2", "tid": TENANT_GUID, "email": "not-an-email"}
    )
    assert invalid.email is None
    assert urlparse(invalid.picture or "https://example.invalid").scheme == "https"


def test_oidc_readiness_uses_only_public_database_configuration():
    configuration = MicrosoftOidcConfiguration(
        client_id=CLIENT_ID,
        tenant_id="common",
        redirect_uri="http://localhost:3001/login",
        enabled=True,
    )

    assert is_microsoft_oidc_configured(configuration) is True
    configuration.enabled = False
    assert is_microsoft_oidc_configured(configuration) is False
