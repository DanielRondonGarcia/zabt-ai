# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Isolated MCP OAuth protocol, storage, and ownership coverage."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from mcp.server.auth.provider import AccessToken
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
from starlette.datastructures import Headers

from app.api import deps
from app.api.v1.endpoints import mcp_oauth as oauth_endpoint
from app.core import config, security
from app.core.config import Settings
from app.mcp_server import MCPTokenVerifier
from app.models import (
    MCPAccessToken,
    MCPClientRegistration,
    MCPGrant,
    MCPRefreshToken,
    User,
)
from app.services.mcp_oauth import (
    MCPAuthorizationStateStore,
    MCPOAuthError,
    MCPOAuthService,
    MCP_READ_SCOPE,
    build_pkce_challenge,
    mcp_resource_url,
    oauth_endpoint_url,
    oauth_issuer_url,
    validate_redirect_uri,
)
from app.services.oauth_state import validate_next_path


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.set_calls: list[dict[str, object]] = []
        self.expiries: dict[str, int] = {}

    def set(self, key, value, *, ex, nx):
        self.set_calls.append({"key": key, "value": value, "ex": ex, "nx": nx})
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expiries[key] = ex
        return True

    def get(self, key):
        return self.values.get(key)

    def getdel(self, key):
        return self.values.pop(key, None)

    def eval(self, _script, _num_keys, key, user_id):
        raw = self.values.get(key)
        if raw is None:
            return None
        payload = json.loads(raw)
        if user_id == "__pending__":
            if payload.get("user_id") is not None:
                return False
        elif str(payload.get("user_id")) != str(user_id):
            return False
        return self.values.pop(key)

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key, seconds):
        self.expiries[key] = seconds
        return True


class _ChunkedRequest:
    """Small ASGI request double that intentionally omits Content-Length."""

    def __init__(self, chunks: list[bytes], content_type: str):
        self.headers = Headers({"content-type": content_type})
        self.scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"content-type", content_type.encode("ascii"))],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        self._chunks = chunks

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


@pytest.fixture()
def mcp_oauth_database() -> Generator:
    db_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    json_column = User.__table__.c.language_preferences
    original_type = json_column.type
    json_column.type = JSON()
    try:
        SQLModel.metadata.create_all(
            db_engine,
            tables=[
                User.__table__,
                MCPClientRegistration.__table__,
                MCPGrant.__table__,
                MCPAccessToken.__table__,
                MCPRefreshToken.__table__,
            ],
        )
        with Session(db_engine) as session:
            session.add_all(
                [
                    User(id=1, email="oauth-owner@example.com"),
                    User(id=2, email="oauth-other@example.com"),
                ]
            )
            session.commit()
        yield db_engine
    finally:
        json_column.type = original_type


@pytest.fixture()
def oauth_service(mcp_oauth_database) -> MCPOAuthService:
    return MCPOAuthService(
        mcp_oauth_database,
        MCPAuthorizationStateStore(FakeRedis()),
    )


@pytest.fixture()
def oauth_client(oauth_service, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(oauth_endpoint, "mcp_oauth_service", oauth_service)
    test_app = FastAPI()
    test_app.include_router(oauth_endpoint.router, prefix="/api/v1")
    test_app.include_router(oauth_endpoint.metadata_router)

    def get_test_db():
        with Session(oauth_service.db_engine) as session:
            yield session

    test_app.dependency_overrides[deps.get_db] = get_test_db
    test_app.dependency_overrides[deps.get_optional_current_active_user] = lambda: User(
        id=1,
        email="oauth-owner@example.com",
    )
    test_app.dependency_overrides[deps.get_current_web_active_user] = lambda: User(
        id=1,
        email="oauth-owner@example.com",
    )
    test_app.dependency_overrides[deps.get_current_active_user] = lambda: User(
        id=1,
        email="oauth-owner@example.com",
    )
    with TestClient(test_app) as client:
        yield client


def _register(
    oauth_client: TestClient,
    *,
    grant_types=None,
    scope=MCP_READ_SCOPE,
    redirect_uri="http://127.0.0.1:1455/callback",
):
    payload = {
        "client_name": "Codex Desktop",
        "redirect_uris": [redirect_uri],
        "response_types": ["code"],
        "grant_types": grant_types or ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": "none",
        "scope": scope,
        "code_challenge_methods_supported": ["S256"],
    }
    response = oauth_client.post("/api/v1/mcp/oauth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _authorize_for_consent(oauth_client: TestClient, params: dict[str, str]):
    response = oauth_client.get(
        "/api/v1/mcp/oauth/authorize",
        params=params,
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    query = parse_qs(location.query)
    assert location.path == "/mcp/consent"
    assert set(query) == {"request_id"}
    return response, query["request_id"][0]


def _authorize_and_approve(oauth_client: TestClient, registration: dict[str, object]):
    verifier = "v" * 43
    params = {
        "client_id": registration["client_id"],
        "redirect_uri": registration["redirect_uris"][0],
        "response_type": "code",
        "code_challenge": build_pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "state": "client-state-123",
        "resource": "http://localhost:8000/api/v1/mcp",
        "scope": MCP_READ_SCOPE,
    }
    _, request_id = _authorize_for_consent(oauth_client, params)
    approved = oauth_client.post(
        "/api/v1/mcp/oauth/authorize/approve",
        json={
            "request_id": request_id,
            "approved": True,
        },
    )
    assert approved.status_code == 200, approved.text
    redirect_url = approved.json()["redirect_url"]
    code = parse_qs(urlsplit(redirect_url).query)["code"][0]
    return verifier, code, params


def _token_exchange(oauth_client: TestClient, registration, verifier, code, params):
    return oauth_client.post(
        "/api/v1/mcp/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": params["redirect_uri"],
            "client_id": registration["client_id"],
            "code_verifier": verifier,
            "resource": params["resource"],
        },
    )


def test_dcr_accepts_only_public_s256_clients_and_never_returns_a_secret(oauth_client):
    registration = _register(oauth_client)

    assert registration["client_id"].startswith("zabt_mcp_client_")
    assert registration["token_endpoint_auth_method"] == "none"
    assert registration["code_challenge_methods_supported"] == ["S256"]
    assert "client_secret" not in registration
    assert "client_secret_expires_at" not in registration
    assert oauth_client.post(
        "/api/v1/mcp/oauth/register",
        json={
            "redirect_uris": ["https://client.example/callback*"],
            "token_endpoint_auth_method": "none",
        },
    ).status_code == 400
    assert oauth_client.post(
        "/api/v1/mcp/oauth/register",
        json={
            "redirect_uris": ["https://client.example/callback"],
            "token_endpoint_auth_method": "client_secret_basic",
        },
    ).status_code == 400
    assert oauth_client.post(
        "/api/v1/mcp/oauth/register",
        json={
            "redirect_uris": ["https://client.example/callback"],
            "client_secret": "must-not-be-accepted",
        },
    ).status_code == 400
    assert oauth_client.post(
        "/api/v1/mcp/oauth/register",
        json={
            "redirect_uris": ["https://client.example/callback"],
            "unrecognized_metadata": "must-not-be-accepted",
        },
    ).status_code == 400


@pytest.mark.parametrize("scope", ["read write", "write read"])
def test_codex_dcr_scope_is_canonicalized_and_only_mcp_scope_is_returned(
    oauth_client,
    scope,
):
    response = oauth_client.post(
        "/api/v1/mcp/oauth/register",
        json={
            "client_name": "Codex",
            "redirect_uris": ["http://127.0.0.1:1455/callback/state-123"],
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none",
            "application_type": "native",
            "response_types": ["code"],
            "scope": scope,
            "code_challenge_methods_supported": ["S256"],
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["scope"] == MCP_READ_SCOPE
    assert response.json()["application_type"] == "native"
    assert response.json()["client_name"] == "Codex"
    assert response.json()["redirect_uris"] == ["http://127.0.0.1:1455/callback/state-123"]


def test_dcr_accepts_web_application_type_with_https_redirect(oauth_client):
    response = oauth_client.post(
        "/api/v1/mcp/oauth/register",
        json={
            "application_type": "web",
            "redirect_uris": ["https://client.example/callback"],
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["application_type"] == "web"
    assert response.json()["scope"] == MCP_READ_SCOPE


@pytest.mark.parametrize("application_type", ["desktop", "Native", "", None, 1, {}])
def test_dcr_rejects_invalid_application_type(oauth_client, application_type):
    response = oauth_client.post(
        "/api/v1/mcp/oauth/register",
        json={
            "application_type": application_type,
            "redirect_uris": ["http://127.0.0.1:1455/callback"],
        },
    )

    assert response.status_code == 400


def test_codex_authorization_and_token_scopes_stay_canonical(
    oauth_client,
    oauth_service,
    mcp_oauth_database,
):
    redirect_uri = "http://127.0.0.1:1455/callback/state-123"
    registration = _register(
        oauth_client,
        scope="read write",
        redirect_uri=redirect_uri,
    )
    verifier = "v" * 43
    params = {
        "client_id": registration["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "code_challenge": build_pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "state": "codex-state-123",
        "resource": "http://localhost:8000/api/v1/mcp",
        "scope": "read write",
    }

    _, request_id = _authorize_for_consent(oauth_client, params)

    consent = oauth_client.get(
        f"/api/v1/mcp/oauth/authorize/request/{request_id}"
    )
    assert consent.status_code == 200, consent.text
    assert consent.json()["scope"] == MCP_READ_SCOPE

    approved = oauth_client.post(
        "/api/v1/mcp/oauth/authorize/approve",
        json={"request_id": request_id, "approved": True},
    )
    assert approved.status_code == 200, approved.text
    code = parse_qs(urlsplit(approved.json()["redirect_url"]).query)["code"][0]

    exchanged = oauth_client.post(
        "/api/v1/mcp/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": registration["client_id"],
            "code_verifier": verifier,
            "resource": params["resource"],
            "scope": "read write",
        },
    )
    assert exchanged.status_code == 200, exchanged.text
    assert exchanged.json()["scope"] == MCP_READ_SCOPE

    with Session(mcp_oauth_database) as session:
        stored_registration = session.exec(select(MCPClientRegistration)).one()
        grant = session.exec(select(MCPGrant)).one()
    assert stored_registration.scope == MCP_READ_SCOPE
    assert grant.scope == MCP_READ_SCOPE
    resolved_token = oauth_service.resolve_access_token(exchanged.json()["access_token"])
    assert resolved_token is not None
    assert resolved_token.scope == MCP_READ_SCOPE


@pytest.mark.parametrize(
    "scope",
    [
        "mcp:write",
        "admin",
        "write",
        "read read",
        "read  write",
        "read\twrite",
        "read write admin",
    ],
)
def test_unsupported_or_malformed_scopes_are_rejected_at_registration_and_authorization(
    oauth_client,
    scope,
):
    dcr = oauth_client.post(
        "/api/v1/mcp/oauth/register",
        json={
            "client_name": "Codex",
            "redirect_uris": ["http://127.0.0.1:1455/callback/state-123"],
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none",
            "response_types": ["code"],
            "scope": scope,
        },
    )
    assert dcr.status_code == 400

    registration = _register(oauth_client)
    authorization = oauth_client.get(
        "/api/v1/mcp/oauth/authorize",
        follow_redirects=False,
        params={
            "client_id": registration["client_id"],
            "redirect_uri": registration["redirect_uris"][0],
            "response_type": "code",
            "code_challenge": build_pkce_challenge("v" * 43),
            "code_challenge_method": "S256",
            "resource": "http://localhost:8000/api/v1/mcp",
            "scope": scope,
        },
    )
    assert authorization.status_code == 400


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///tmp/callback",
        "mailto:oauth@example.com",
        "https://client.example/callback*",
        "http://client.example/callback",
    ],
)
def test_public_dcr_rejects_unsafe_redirects_for_native_application_type(
    oauth_client,
    redirect_uri,
):
    with pytest.raises(MCPOAuthError):
        validate_redirect_uri(redirect_uri)

    response = oauth_client.post(
        "/api/v1/mcp/oauth/register",
        json={
            "application_type": "native",
            "client_name": "Unsafe client",
            "redirect_uris": [redirect_uri],
        },
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://client.example/callback",
        "http://localhost:1455/callback",
        "http://127.0.0.1:1455/callback?channel=desktop",
        "http://[::1]:1455/callback",
        "com.example.desktop://oauth/callback",
        "com.example.desktop:/oauth/callback",
        "com.example.enterprise.desktop.client://oauth/callback",
    ],
)
def test_redirect_uri_accepts_https_loopback_and_safe_native_targets(
    oauth_client,
    redirect_uri,
):
    assert validate_redirect_uri(redirect_uri) == redirect_uri
    response = oauth_client.post(
        "/api/v1/mcp/oauth/register",
        json={"client_name": "Safe client", "redirect_uris": [redirect_uri]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["redirect_uris"] == [redirect_uri]


def test_metadata_uses_canonical_resource_and_issuer(oauth_client, monkeypatch):
    monkeypatch.setattr(config.settings, "MCP_PUBLIC_URL", "https://api.example/mcp")
    monkeypatch.setattr(config.settings, "MCP_OAUTH_ISSUER", "https://api.example/mcp/oauth")

    server = oauth_client.get("/.well-known/oauth-authorization-server")
    path_scoped_server = oauth_client.get(
        "/.well-known/oauth-authorization-server/api/v1/mcp/oauth"
    )
    resource = oauth_client.get("/.well-known/oauth-protected-resource/api/v1/mcp")

    assert server.status_code == 200
    assert path_scoped_server.status_code == 200
    assert resource.status_code == 200
    assert server.headers["cache-control"] == "no-store"
    assert server.json() == {
        "issuer": "https://api.example/mcp/oauth",
        "authorization_endpoint": "https://api.example/mcp/oauth/authorize",
        "token_endpoint": "https://api.example/mcp/oauth/token",
        "registration_endpoint": "https://api.example/mcp/oauth/register",
        "revocation_endpoint": "https://api.example/mcp/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp:read"],
    }
    assert resource.json() == {
        "resource": "https://api.example/mcp",
        "authorization_servers": ["https://api.example/mcp/oauth"],
        "scopes_supported": ["mcp:read"],
        "bearer_methods_supported": ["header"],
    }


def test_mcp_transport_urls_require_https_for_non_loopback_non_development(monkeypatch):
    monkeypatch.setattr(config.settings, "AUTH_ENVIRONMENT", "production")
    monkeypatch.setattr(config.settings, "MCP_PUBLIC_URL", "http://api.example/mcp")
    monkeypatch.setattr(config.settings, "MCP_OAUTH_ISSUER", "https://api.example/mcp/oauth")

    with pytest.raises(MCPOAuthError):
        mcp_resource_url()

    monkeypatch.setattr(config.settings, "MCP_PUBLIC_URL", "https://api.example/mcp")
    monkeypatch.setattr(config.settings, "MCP_OAUTH_ISSUER", "http://api.example/mcp/oauth")
    with pytest.raises(MCPOAuthError):
        oauth_issuer_url()

    monkeypatch.setattr(config.settings, "MCP_OAUTH_ISSUER", "https://api.example/mcp/oauth")
    assert oauth_endpoint_url("token") == "https://api.example/mcp/oauth/token"

    monkeypatch.setattr(config.settings, "AUTH_ENVIRONMENT", "development")
    monkeypatch.setattr(config.settings, "MCP_PUBLIC_URL", "http://localhost:8000/api/v1/mcp")
    monkeypatch.setattr(
        config.settings,
        "MCP_OAUTH_ISSUER",
        "http://localhost:8000/api/v1/mcp/oauth",
    )
    assert mcp_resource_url() == "http://localhost:8000/api/v1/mcp"
    assert oauth_endpoint_url("token") == "http://localhost:8000/api/v1/mcp/oauth/token"


def test_settings_reject_production_http_mcp_public_url():
    with pytest.raises(ValueError, match="MCP_PUBLIC_URL"):
        Settings(
            _env_file=None,
            AUTH_JWT_SECRET="test-only-placeholder-not-a-credential-1234567890",
            AUTH_ENVIRONMENT="production",
            AUTH_COOKIE_SECURE=True,
            MCP_PUBLIC_URL="http://api.example/mcp",
        )


def test_unauthenticated_authorization_can_be_approved_or_denied_in_browser(
    oauth_client,
    monkeypatch,
):
    registration = _register(oauth_client)
    monkeypatch.setattr(
        oauth_endpoint,
        "_rate_limit",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(config.settings, "APP_URL", "http://localhost:3001")
    oauth_client.app.dependency_overrides[deps.get_optional_current_active_user] = lambda: None
    params = {
        "client_id": registration["client_id"],
        "redirect_uri": registration["redirect_uris"][0],
        "response_type": "code",
        "code_challenge": "a" * 43,
        "code_challenge_method": "S256",
        "state": "browser-state",
        "resource": "http://localhost:8000/api/v1/mcp",
        "scope": MCP_READ_SCOPE,
    }
    response, request_id = _authorize_for_consent(oauth_client, params)
    consent_location = urlsplit(response.headers["location"])
    consent_query = parse_qs(consent_location.query)
    assert consent_location.scheme == "http"
    assert consent_location.hostname == "localhost"
    assert consent_location.port == 3001
    assert consent_location.path == "/mcp/consent"
    assert consent_location.fragment == ""
    assert consent_query == {"request_id": [request_id]}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.content == b""
    redirect_target = response.text + response.headers["location"]
    for secret in (
        params["state"],
        params["code_challenge"],
        params["client_id"],
        params["redirect_uri"],
        params["resource"],
    ):
        assert secret not in redirect_target
    assert "code" not in consent_query

    details = oauth_client.get(
        f"/api/v1/mcp/oauth/authorize/request/{request_id}"
    )
    assert details.status_code == 200, details.text
    assert details.json()["redirect_uri"] == params["redirect_uri"]
    assert details.json()["authorization_request_id"] == request_id
    assert set(details.json()) == {
        "status",
        "authorization_request_id",
        "client_name",
        "redirect_uri",
        "resource",
        "scope",
    }

    approved = oauth_client.post(
        "/api/v1/mcp/oauth/authorize/approve",
        json={"request_id": request_id, "approved": True},
    )
    assert approved.status_code == 200, approved.text
    approved_redirect = approved.json()["redirect_url"]
    assert parse_qs(urlsplit(approved_redirect).query)["state"] == ["browser-state"]

    _, denied_request_id = _authorize_for_consent(oauth_client, params)
    denied = oauth_client.post(
        "/api/v1/mcp/oauth/authorize/approve",
        json={"request_id": denied_request_id, "approved": False},
    )
    assert denied.status_code == 200, denied.text
    denied_redirect = denied.json()["redirect_url"]
    denied_query = parse_qs(urlsplit(denied_redirect).query)
    assert denied_query["error"] == ["access_denied"]
    assert denied_query["state"] == ["browser-state"]


def test_oauth_approval_rejects_legacy_bearer_without_web_cookie(oauth_client):
    registration = _register(oauth_client)
    _, request_id = _authorize_for_consent(
        oauth_client,
        {
            "client_id": registration["client_id"],
            "redirect_uri": registration["redirect_uris"][0],
            "response_type": "code",
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
            "resource": "http://localhost:8000/api/v1/mcp",
            "scope": MCP_READ_SCOPE,
        },
    )
    oauth_client.app.dependency_overrides.pop(deps.get_current_web_active_user)
    response = oauth_client.post(
        "/api/v1/mcp/oauth/authorize/approve",
        headers={"Authorization": "Bearer legacy-mcp-token"},
        json={"request_id": request_id, "approved": True},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_web_cookie_can_approve_pending_request(oauth_client, monkeypatch):
    registration = _register(oauth_client)
    _, request_id = _authorize_for_consent(
        oauth_client,
        {
            "client_id": registration["client_id"],
            "redirect_uri": registration["redirect_uris"][0],
            "response_type": "code",
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
            "resource": "http://localhost:8000/api/v1/mcp",
            "scope": MCP_READ_SCOPE,
        },
    )
    oauth_client.app.dependency_overrides.pop(deps.get_current_web_active_user)
    monkeypatch.setattr(config.settings, "AUTH_ALLOWED_ORIGINS", "http://testserver")
    access_token, _ = security.create_access_token(1)
    oauth_client.cookies.set(config.settings.AUTH_ACCESS_COOKIE_NAME, access_token)

    approved = oauth_client.post(
        "/api/v1/mcp/oauth/authorize/approve",
        headers={"Origin": "http://testserver"},
        json={"request_id": request_id, "approved": True},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"


@pytest.mark.parametrize(
    "next_path",
    ["https://evil.example/", "//evil.example/", "/\\\\evil.example", "/mcp/consent#fragment"],
)
def test_login_next_path_validation_rejects_external_or_fragment_targets(next_path):
    with pytest.raises(ValueError, match="Invalid next path"):
        validate_next_path(next_path)


def test_login_next_path_validation_accepts_consent_path():
    assert validate_next_path("/mcp/consent?request_id=" + "a" * 32) == (
        "/mcp/consent?request_id=" + "a" * 32
    )


def test_authorization_request_consumption_is_bound_to_current_user(oauth_service):
    registration = oauth_service.register_client(
        client_name="Owner binding",
        redirect_uris=["http://127.0.0.1:1455/callback"],
    )
    transaction = oauth_service.create_authorization_request(
        client_id=registration["client_id"],
        redirect_uri=registration["redirect_uris"][0],
        response_type="code",
        code_challenge=build_pkce_challenge("v" * 43),
        code_challenge_method="S256",
        state="state",
        resource="http://localhost:8000/api/v1/mcp",
        scope=MCP_READ_SCOPE,
        user_id=1,
    )
    with pytest.raises(ValueError, match="invalid or expired"):
        oauth_service.approve_authorization_request(
            transaction.request_id,
            user_id=2,
            approved=True,
        )
    _, code = oauth_service.approve_authorization_request(
        transaction.request_id,
        user_id=1,
        approved=True,
    )
    assert code is not None


def test_pkce_success_failure_and_authorization_code_replay(oauth_client):
    registration = _register(oauth_client)
    verifier, code, params = _authorize_and_approve(oauth_client, registration)

    wrong = _token_exchange(oauth_client, registration, "w" * 43, code, params)
    assert wrong.status_code == 400
    assert wrong.json()["error"] == "invalid_grant"

    replay = _token_exchange(oauth_client, registration, verifier, code, params)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_revoked_grant_rejects_stale_code_and_fresh_approval_reactivates_generation(
    oauth_client,
    oauth_service,
    mcp_oauth_database,
):
    registration = _register(oauth_client)
    verifier, stale_code, params = _authorize_and_approve(oauth_client, registration)

    with Session(mcp_oauth_database) as session:
        grant = session.exec(select(MCPGrant)).one()
        initial_generation = grant.generation
        grant_id = grant.id

    revoked = oauth_client.delete(f"/api/v1/mcp/oauth/apps/{grant_id}")
    assert revoked.status_code == 204

    stale_exchange = _token_exchange(oauth_client, registration, verifier, stale_code, params)
    assert stale_exchange.status_code == 400
    assert stale_exchange.json()["error"] == "invalid_grant"

    with Session(mcp_oauth_database) as session:
        revoked_grant = session.get(MCPGrant, grant_id)
        assert revoked_grant is not None
        assert revoked_grant.revoked_at is not None
        assert revoked_grant.generation == initial_generation + 1

    fresh_verifier, fresh_code, fresh_params = _authorize_and_approve(oauth_client, registration)
    with Session(mcp_oauth_database) as session:
        reactivated_grant = session.get(MCPGrant, grant_id)
        assert reactivated_grant is not None
        assert reactivated_grant.revoked_at is None
        assert reactivated_grant.generation == initial_generation + 1

    fresh_exchange = _token_exchange(
        oauth_client,
        registration,
        fresh_verifier,
        fresh_code,
        fresh_params,
    )
    assert fresh_exchange.status_code == 200, fresh_exchange.text
    assert oauth_service.resolve_access_token(fresh_exchange.json()["access_token"]) is not None


def test_refresh_replay_revokes_pending_authorization_code(
    oauth_client,
    oauth_service,
):
    registration = _register(oauth_client)
    verifier, first_code, params = _authorize_and_approve(oauth_client, registration)
    first_exchange = _token_exchange(oauth_client, registration, verifier, first_code, params)
    assert first_exchange.status_code == 200, first_exchange.text
    first_tokens = first_exchange.json()

    pending_verifier, pending_code, pending_params = _authorize_and_approve(
        oauth_client,
        registration,
    )
    rotated = oauth_client.post(
        "/api/v1/mcp/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first_tokens["refresh_token"],
            "client_id": registration["client_id"],
            "resource": params["resource"],
        },
    )
    assert rotated.status_code == 200, rotated.text

    replay = oauth_client.post(
        "/api/v1/mcp/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first_tokens["refresh_token"],
            "client_id": registration["client_id"],
            "resource": params["resource"],
        },
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"

    stale_exchange = _token_exchange(
        oauth_client,
        registration,
        pending_verifier,
        pending_code,
        pending_params,
    )
    assert stale_exchange.status_code == 400
    assert stale_exchange.json()["error"] == "invalid_grant"
    assert oauth_service.resolve_access_token(rotated.json()["access_token"]) is None


def test_resource_redirect_and_scope_bindings_are_strict(oauth_client):
    registration = _register(oauth_client)
    verifier = "v" * 43
    common = {
        "client_id": registration["client_id"],
        "redirect_uri": registration["redirect_uris"][0],
        "response_type": "code",
        "code_challenge": build_pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "resource": "https://other.example/mcp",
        "scope": MCP_READ_SCOPE,
    }
    assert oauth_client.get(
        "/api/v1/mcp/oauth/authorize", params=common, follow_redirects=False
    ).status_code == 400
    common["resource"] = "http://localhost:8000/api/v1/mcp"
    common["redirect_uri"] = "http://127.0.0.1:9999/wrong"
    assert oauth_client.get(
        "/api/v1/mcp/oauth/authorize", params=common, follow_redirects=False
    ).status_code == 400
    common["redirect_uri"] = registration["redirect_uris"][0]
    common["scope"] = "mcp:read mcp:write"
    assert oauth_client.get(
        "/api/v1/mcp/oauth/authorize", params=common, follow_redirects=False
    ).status_code == 400

    verifier, code, params = _authorize_and_approve(oauth_client, registration)
    mismatched_resource = _token_exchange(
        oauth_client,
        registration,
        verifier,
        code,
        {**params, "resource": "http://localhost:8000/api/v1/mcp/"},
    )
    assert mismatched_resource.status_code == 400


def test_token_response_stores_only_hashes_and_supports_refresh_rotation(
    oauth_client,
    oauth_service,
    mcp_oauth_database,
):
    registration = _register(oauth_client)
    verifier, code, params = _authorize_and_approve(oauth_client, registration)
    exchanged = _token_exchange(oauth_client, registration, verifier, code, params)
    assert exchanged.status_code == 200, exchanged.text
    first = exchanged.json()
    assert first["token_type"] == "Bearer"
    assert first["scope"] == MCP_READ_SCOPE
    assert first["refresh_token"]
    assert first["access_token"]
    assert first["access_token"] not in exchanged.text.split("access_token")[0]

    revoked = oauth_client.post(
        "/api/v1/mcp/oauth/revoke",
        data={
            "token": first["access_token"],
            "client_id": registration["client_id"],
        },
    )
    assert revoked.status_code == 200
    assert oauth_service.resolve_access_token(first["access_token"]) is None

    rotated = oauth_client.post(
        "/api/v1/mcp/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": registration["client_id"],
            "resource": params["resource"],
        },
    )
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()
    assert second["access_token"] != first["access_token"]
    assert second["refresh_token"] != first["refresh_token"]

    replay = oauth_client.post(
        "/api/v1/mcp/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": registration["client_id"],
            "resource": params["resource"],
        },
    )
    assert replay.status_code == 400
    assert oauth_service.resolve_access_token(second["access_token"]) is None

    with Session(mcp_oauth_database) as session:
        access_tokens = session.exec(select(MCPAccessToken)).all()
        refresh_tokens = session.exec(select(MCPRefreshToken)).all()
    assert all(first["access_token"] not in token.token_hash for token in access_tokens)
    assert all(first["refresh_token"] not in token.token_hash for token in refresh_tokens)


def test_linked_app_listing_and_revocation_are_owner_scoped(
    oauth_client,
    oauth_service,
    monkeypatch: pytest.MonkeyPatch,
):
    registration = _register(oauth_client)
    verifier, code, params = _authorize_and_approve(oauth_client, registration)
    exchanged = _token_exchange(oauth_client, registration, verifier, code, params)
    assert exchanged.status_code == 200

    linked = oauth_client.get("/api/v1/mcp/oauth/apps")
    assert linked.status_code == 200
    assert len(linked.json()) == 1
    linked_app = linked.json()[0]
    assert linked_app["client_name"] == "Codex Desktop"
    assert linked_app["redirect_uris"] == [params["redirect_uri"]]
    assert linked_app["scope"] == MCP_READ_SCOPE
    assert linked_app["created_at"]
    assert linked_app["last_used_at"]
    assert linked_app["revoked_at"] is None
    assert "access_token" not in linked.text
    assert "refresh_token" not in linked.text
    grant_id = linked_app["grant_id"]

    oauth_client.app.dependency_overrides[deps.get_current_active_user] = lambda: User(
        id=2,
        email="oauth-other@example.com",
    )
    foreign = oauth_client.delete(f"/api/v1/mcp/oauth/apps/{grant_id}")
    assert foreign.status_code == 404

    oauth_client.app.dependency_overrides[deps.get_current_active_user] = lambda: User(
        id=1,
        email="oauth-owner@example.com",
    )
    revoked = oauth_client.delete(f"/api/v1/mcp/oauth/apps/{grant_id}")
    assert revoked.status_code == 204
    assert oauth_service.resolve_access_token(exchanged.json()["access_token"]) is None
    revoked_listing = oauth_client.get("/api/v1/mcp/oauth/apps")
    assert revoked_listing.status_code == 200
    assert revoked_listing.json()[0]["revoked_at"]


class _LegacyVerifier:
    def __init__(self, token: str):
        self.token = token

    def resolve_bearer_token(self, token: str):
        if token != self.token:
            return None
        return type(
            "LegacyRecord",
            (),
            {
                "id": 7,
                "user_id": 1,
                "expires_at": datetime.utcnow(),
            },
        )()


@pytest.mark.asyncio
async def test_verifier_resolves_oauth_resource_binding_and_legacy_fallback(oauth_service):
    registration = oauth_service.register_client(
        client_name="Verifier",
        redirect_uris=["http://127.0.0.1:1455/callback"],
        grant_types=["authorization_code", "refresh_token"],
    )
    verifier = "v" * 43
    transaction = oauth_service.create_authorization_request(
        client_id=registration["client_id"],
        redirect_uri=registration["redirect_uris"][0],
        response_type="code",
        code_challenge=build_pkce_challenge(verifier),
        code_challenge_method="S256",
        state="state",
        resource="http://localhost:8000/api/v1/mcp",
        scope=MCP_READ_SCOPE,
        user_id=1,
    )
    _, code = oauth_service.approve_authorization_request(
        transaction.request_id,
        user_id=1,
        approved=True,
    )
    pair = oauth_service.exchange_authorization_code(
        code=code,
        client_id=registration["client_id"],
        redirect_uri=registration["redirect_uris"][0],
        code_verifier=verifier,
        resource="http://localhost:8000/api/v1/mcp",
    )

    oauth_verified = await MCPTokenVerifier(
        _LegacyVerifier("legacy"),
        oauth_service,
    ).verify_token(pair.access_token)
    assert isinstance(oauth_verified, AccessToken)
    assert oauth_verified.subject == "1"
    assert oauth_verified.scopes == [MCP_READ_SCOPE]
    assert oauth_verified.resource == "http://localhost:8000/api/v1/mcp"
    assert oauth_verified.claims["aud"] == "http://localhost:8000/api/v1/mcp"

    legacy = await MCPTokenVerifier(_LegacyVerifier("legacy")).verify_token("legacy")
    assert legacy is not None
    assert legacy.client_id == "mcp-token-7"
    assert legacy.scopes == [MCP_READ_SCOPE]


def test_state_and_code_storage_hashes_opaque_values(oauth_service):
    registration = oauth_service.register_client(
        client_name="Storage",
        redirect_uris=["http://127.0.0.1:1455/callback"],
    )
    verifier = "v" * 43
    transaction = oauth_service.create_authorization_request(
        client_id=registration["client_id"],
        redirect_uri=registration["redirect_uris"][0],
        response_type="code",
        code_challenge=build_pkce_challenge(verifier),
        code_challenge_method="S256",
        state="state",
        resource="http://localhost:8000/api/v1/mcp",
        scope=MCP_READ_SCOPE,
        user_id=1,
    )
    store = oauth_service.state_store
    request_record = store._redis.set_calls[0]
    assert transaction.request_id not in request_record["key"]
    assert transaction.request_id not in request_record["value"]
    _, code = oauth_service.approve_authorization_request(
        transaction.request_id,
        user_id=1,
        approved=True,
    )
    code_record = store._redis.set_calls[1]
    assert code not in code_record["key"]
    assert code not in code_record["value"]
    assert request_record["ex"] == 600
    assert code_record["ex"] == 60


@pytest.mark.asyncio
async def test_oauth_body_limits_reject_chunked_bodies_without_content_length(
    oauth_client,
    monkeypatch,
):
    monkeypatch.setattr(oauth_endpoint, "_rate_limit", lambda *args, **kwargs: None)
    secret = "must-not-appear-in-an-error"

    dcr = await oauth_endpoint.register_client(
        _ChunkedRequest(
            [b'{"client_name":"', secret.encode(), b'x' * 16_384, b'"}'],
            "application/json",
        ),
        Response(),
    )
    approval = await oauth_endpoint.approve_authorization(
        _ChunkedRequest(
            [b'{"request_id":"', b'x' * 8_192, b'","approved":true}'],
            "application/json",
        ),
        Response(),
    )
    token = await oauth_endpoint.token(
        _ChunkedRequest([b"grant_type=", b"x" * 8_192], "application/x-www-form-urlencoded"),
        Response(),
    )
    revoke = await oauth_endpoint.revoke(
        _ChunkedRequest([b"token=", b"x" * 8_192], "application/x-www-form-urlencoded"),
        Response(),
    )

    for response in (dcr, approval, token, revoke):
        assert response.status_code == 400
        assert json.loads(response.body)["error"] == "invalid_request"
        assert secret.encode() not in response.body
