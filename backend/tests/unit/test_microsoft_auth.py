# SPDX-License-Identifier: AGPL-3.0-only
"""Public OIDC endpoint, account-resolution, cookie, and admin tests."""

from __future__ import annotations

from datetime import datetime
import json

import pytest
from fastapi import Request, Response
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.api.v1.endpoints import auth as auth_endpoint
from app.core.config import settings
from app.models import (
    ExternalIdentity,
    ExternalIdentityProvider,
    MicrosoftOidcConfiguration,
    User,
)
from app.services import auth as auth_service
from app.services.microsoft_oidc import (
    MicrosoftOidcAccountConflictError,
    MicrosoftOidcExternalIdentityConflictError,
    MicrosoftOidcIdentity,
    link_external_identity,
    resolve_or_create_user,
)


CLIENT_ID = "11111111-1111-4111-8111-111111111111"


class ExecResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDb:
    def __init__(self, exec_rows=None, users=None):
        self.exec_rows = list(exec_rows or [])
        self.users = dict(users or {})
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    def exec(self, _statement):
        return ExecResult(self.exec_rows.pop(0) if self.exec_rows else [])

    def get(self, model, obj_id):
        return self.users.get(obj_id)

    def add(self, obj):
        if isinstance(obj, User) and obj.id is None:
            obj.id = 20
        self.added.append(obj)

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def make_user(**overrides) -> User:
    values = {
        "id": 7,
        "email": "user@example.com",
        "full_name": "Local User",
        "is_active": True,
        "password_hash": "hash",
    }
    values.update(overrides)
    return User(**values)


def make_identity(**overrides) -> ExternalIdentity:
    values = {
        "id": 1,
        "user_id": 7,
        "provider": ExternalIdentityProvider.MICROSOFT,
        "tenant_id": "tenant-1",
        "subject": "subject-1",
        "email": "user@example.com",
    }
    values.update(overrides)
    return ExternalIdentity(**values)


def make_oidc_identity(**overrides) -> MicrosoftOidcIdentity:
    values = {
        "subject": "subject-1",
        "tenant_id": "tenant-1",
        "email": "user@example.com",
        "full_name": "Microsoft User",
        "picture": None,
    }
    values.update(overrides)
    return MicrosoftOidcIdentity(**values)


def make_configuration(**overrides) -> MicrosoftOidcConfiguration:
    values = {
        "id": 1,
        "client_id": CLIENT_ID,
        "tenant_id": "common",
        "redirect_uri": "http://localhost:3001/login",
        "enabled": True,
        "created_at": datetime(2026, 10, 7),
        "updated_at": datetime(2026, 10, 7),
        "updated_by": 7,
    }
    values.update(overrides)
    return MicrosoftOidcConfiguration(**values)


def make_request(id_token: str | None = None) -> Request:
    body = json.dumps({"id_token": id_token}).encode() if id_token is not None else b""

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/microsoft/oidc/exchange",
            "headers": [
                (b"origin", b"http://localhost:3001"),
                (b"content-length", str(len(body)).encode()),
            ],
        },
        receive,
    )


def test_anonymous_oidc_rejects_existing_local_email_without_linking():
    user = make_user()
    db = FakeDb(exec_rows=[[], [user]])

    with pytest.raises(MicrosoftOidcAccountConflictError):
        resolve_or_create_user(db, make_oidc_identity())

    assert db.added == []


def test_explicit_link_allows_different_provider_email_for_active_user():
    user = make_user()
    db = FakeDb(exec_rows=[[]])

    resolved = link_external_identity(
        db,
        make_oidc_identity(email="different@example.com"),
        user,
    )

    assert resolved is user
    linked = next(item for item in db.added if isinstance(item, ExternalIdentity))
    assert linked.user_id == user.id
    assert linked.provider == ExternalIdentityProvider.MICROSOFT
    assert linked.email == "different@example.com"


def test_explicit_link_rejects_identity_already_owned_by_another_user():
    existing = make_identity(user_id=99)
    with pytest.raises(MicrosoftOidcExternalIdentityConflictError):
        link_external_identity(
            FakeDb(exec_rows=[[existing]]),
            make_oidc_identity(),
            make_user(id=7),
        )


def test_explicit_link_rejects_inactive_user():
    with pytest.raises(auth_service.InactiveUserError):
        link_external_identity(
            FakeDb(exec_rows=[[]]),
            make_oidc_identity(),
            make_user(is_active=False),
        )


def test_validated_identity_creates_external_only_user_without_password():
    db = FakeDb(exec_rows=[[], []])

    user = resolve_or_create_user(db, make_oidc_identity(email="new@example.com"))

    assert user.email == "new@example.com"
    assert user.password_hash is None
    assert user.supabase_id is None
    assert user.is_active is True
    assert any(isinstance(item, ExternalIdentity) for item in db.added)
    assert db.flushes == 2


def test_existing_external_identity_updates_last_login_and_rejects_inactive_owner():
    existing = make_identity()
    user = make_user()
    db = FakeDb(exec_rows=[[existing]], users={user.id: user})

    assert resolve_or_create_user(db, make_oidc_identity(email="updated@example.com")) is user
    assert existing.email == "updated@example.com"
    assert existing.last_login_at is not None

    inactive = make_user(is_active=False)
    with pytest.raises(auth_service.InactiveUserError):
        resolve_or_create_user(
            FakeDb(exec_rows=[[make_identity()]], users={inactive.id: inactive}),
            make_oidc_identity(),
        )


def test_public_status_contains_oidc_values_and_separate_graph_readiness(
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = make_configuration()
    db = FakeDb(exec_rows=[[configuration]])
    monkeypatch.setattr(auth_endpoint, "is_token_storage_configured", lambda: False)
    monkeypatch.setattr(auth_endpoint.settings, "MICROSOFT_CLIENT_ID", "")
    monkeypatch.setattr(auth_endpoint.settings, "MICROSOFT_CLIENT_SECRET", "")
    monkeypatch.setattr(auth_endpoint.settings, "MICROSOFT_REDIRECT_URI", "")

    http_response = Response()
    response = auth_endpoint.microsoft_oidc_status(http_response, db)

    assert response.configured is True
    assert response.client_id == CLIENT_ID
    assert response.tenant == "common"
    assert response.redirect_uri.startswith("http://localhost")
    assert response.scopes == ["openid", "profile", "email"]
    assert response.graph_configured is False
    assert response.token_storage_configured is False
    assert not hasattr(response, "client_secret")
    assert http_response.headers["cache-control"] == "no-store"


def test_admin_configuration_get_and_put_return_no_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    admin = make_user(is_admin=True)
    configuration = make_configuration()
    updated = make_configuration(updated_by=admin.id, tenant_id="organizations")
    db = FakeDb(exec_rows=[[configuration], [configuration], [updated], [updated], [updated]])
    monkeypatch.setattr(auth_endpoint, "is_token_storage_configured", lambda: True)
    monkeypatch.setattr(auth_endpoint, "_graph_configuration_configured", lambda: True)
    monkeypatch.setattr(
        auth_endpoint.security,
        "validate_request_origin",
        lambda request: "http://localhost:3001",
    )

    get_http_response = Response()
    get_response = auth_endpoint.microsoft_oidc_configuration(get_http_response, admin, db)
    assert get_response.can_manage is True
    assert get_response.is_admin is True
    assert "client_secret" not in get_response.model_dump()
    assert get_http_response.headers["cache-control"] == "no-store"

    upsert_kwargs = {}

    def fake_upsert(*args, **kwargs):
        upsert_kwargs.update(kwargs)
        return updated

    monkeypatch.setattr(auth_endpoint, "upsert_microsoft_oidc_configuration", fake_upsert)
    put_http_response = Response()
    put_response = auth_endpoint.update_microsoft_oidc_configuration(
        auth_endpoint.MicrosoftOidcConfigurationUpdate(
            client_id=CLIENT_ID,
            tenant="organizations",
            redirect_uri="http://localhost:3001/login",
        ),
        make_request(),
        put_http_response,
        admin,
        db,
    )

    assert put_response.tenant == "organizations"
    assert db.commits == 1
    assert put_http_response.headers["cache-control"] == "no-store"
    assert upsert_kwargs["spa_origin"] == "http://localhost:3001"


def test_non_admin_can_view_and_claim_global_configuration_when_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    user = make_user(is_admin=False)
    saved = make_configuration(updated_by=user.id)
    monkeypatch.setattr(
        auth_endpoint.security,
        "validate_request_origin",
        lambda request: "http://localhost:3001",
    )

    view_response = auth_endpoint.microsoft_oidc_configuration(
        Response(),
        user,
        FakeDb(exec_rows=[[], []]),
    )
    assert view_response.can_manage is True
    assert view_response.is_admin is False

    db = FakeDb(exec_rows=[[]])
    upsert_kwargs = {}

    def fake_upsert(*args, **kwargs):
        upsert_kwargs.update(kwargs)
        return saved

    monkeypatch.setattr(auth_endpoint, "upsert_microsoft_oidc_configuration", fake_upsert)
    auth_endpoint.update_microsoft_oidc_configuration(
        auth_endpoint.MicrosoftOidcConfigurationUpdate(
            client_id=CLIENT_ID,
            tenant="common",
            redirect_uri="http://localhost:3001/login",
        ),
        make_request(),
        Response(),
        user,
        db,
    )

    assert user.is_admin is True
    assert db.commits == 1
    assert upsert_kwargs["updated_by"] == user.id


def test_non_admin_cannot_claim_or_overwrite_after_configuration_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    user = make_user(is_admin=False)
    configuration = make_configuration()
    db = FakeDb(exec_rows=[[configuration]])
    monkeypatch.setattr(
        auth_endpoint.security,
        "validate_request_origin",
        lambda request: "http://localhost:3001",
    )
    monkeypatch.setattr(
        auth_endpoint,
        "upsert_microsoft_oidc_configuration",
        lambda *args, **kwargs: pytest.fail("a non-admin must not overwrite configuration"),
    )

    with pytest.raises(Exception) as error:
        auth_endpoint.update_microsoft_oidc_configuration(
            auth_endpoint.MicrosoftOidcConfigurationUpdate(
                client_id=CLIENT_ID,
                tenant="common",
                redirect_uri="http://localhost:3001/login",
            ),
            make_request(),
            Response(),
            user,
            db,
        )

    assert error.value.status_code == 403
    assert db.commits == 0
    assert user.is_admin is False


def test_non_admin_loses_read_only_manage_claim_after_configuration_exists():
    user = make_user(is_admin=False)
    configuration = make_configuration()
    response = auth_endpoint.microsoft_oidc_configuration(
        Response(),
        user,
        FakeDb(exec_rows=[[configuration], [configuration]]),
    )

    assert response.can_manage is False
    assert response.is_admin is False


class FakeVerifier:
    def __init__(self, claims):
        self.claims = claims

    async def validate_id_token(self, id_token: str):
        assert id_token == "signed-id-token"
        return self.claims


@pytest.mark.asyncio
async def test_id_token_exchange_issues_existing_web_cookies_without_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = make_configuration()
    user = make_user()
    db = FakeDb(exec_rows=[[configuration]])
    response = Response()
    monkeypatch.setattr(auth_endpoint.security, "validate_request_origin", lambda request: None)
    monkeypatch.setattr(auth_endpoint, "_oidc_verifier", lambda config: FakeVerifier({
        "sub": "subject-1",
        "tid": "tenant-1",
    }))
    monkeypatch.setattr(auth_endpoint, "resolve_or_create_user", lambda db, identity: user)
    monkeypatch.setattr(
        auth_endpoint.auth_service,
        "issue_tokens",
        lambda db, user: auth_service.TokenBundle("access", "refresh", 900),
    )

    result = await auth_endpoint.exchange_microsoft_oidc_token(
        make_request("signed-id-token"),
        response,
        db,
    )

    assert result.user.id == user.id
    set_cookie_headers = [
        value.decode("latin-1")
        for name, value in response.raw_headers
        if name.lower() == b"set-cookie"
    ]
    assert any("zabt_access_token=access" in value for value in set_cookie_headers)
    assert any("zabt_refresh_token=refresh" in value for value in set_cookie_headers)
    assert db.rollbacks == 0


@pytest.mark.asyncio
async def test_anonymous_exchange_returns_static_local_conflict_without_token_echo(
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = make_configuration()
    marker = "SIGNED_TOKEN_MARKER"
    db = FakeDb(exec_rows=[[configuration]])
    monkeypatch.setattr(auth_endpoint.security, "validate_request_origin", lambda request: None)
    monkeypatch.setattr(auth_endpoint, "_oidc_verifier", lambda config: FakeVerifier({
        "sub": "subject-1",
        "tid": "tenant-1",
    }))
    monkeypatch.setattr(
        auth_endpoint,
        "resolve_or_create_user",
        lambda db, identity: (_ for _ in ()).throw(MicrosoftOidcAccountConflictError()),
    )

    with pytest.raises(Exception) as error:
        await auth_endpoint.exchange_microsoft_oidc_token(
            make_request("signed-id-token"),
            Response(),
            db,
        )

    assert error.value.status_code == 409
    assert marker not in str(error.value)
    assert "client_secret" not in str(error.value)


@pytest.mark.asyncio
async def test_explicit_link_validates_id_token_without_issuing_cookies(
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = make_configuration()
    user = make_user()
    db = FakeDb(exec_rows=[[configuration]])
    linked = []
    monkeypatch.setattr(auth_endpoint.security, "validate_request_origin", lambda request: None)
    monkeypatch.setattr(auth_endpoint, "_oidc_verifier", lambda config: FakeVerifier({
        "sub": "subject-1",
        "tid": "tenant-1",
    }))
    monkeypatch.setattr(
        auth_endpoint,
        "link_external_identity",
        lambda db, identity, current_user: linked.append((identity, current_user)),
    )

    result = await auth_endpoint.link_microsoft_oidc_token(
        make_request("signed-id-token"),
        user,
        db,
    )

    assert result == {"status": "linked"}
    assert linked[0][1] is user
    assert db.commits == 1


def test_id_token_request_bounds_input_without_echoing_value(
    monkeypatch: pytest.MonkeyPatch,
):
    marker = "TOKEN_MARKER"
    monkeypatch.setattr(auth_endpoint.settings, "AUTH_ALLOWED_ORIGINS", "http://localhost:3001")
    app = FastAPI()
    app.include_router(auth_endpoint.router, prefix="/auth")
    with TestClient(app) as client:
        response = client.post(
            "/auth/microsoft/oidc/exchange",
            headers={"Origin": "http://localhost:3001"},
            json={"id_token": marker * (auth_endpoint.MAX_MICROSOFT_ID_TOKEN_LENGTH + 1)},
        )
    assert response.status_code == 400
    assert marker not in response.text


def test_legacy_confidential_routes_are_retired():
    app = FastAPI()
    app.include_router(auth_endpoint.router, prefix="/auth")
    with TestClient(app) as client:
        response = client.get("/auth/microsoft/start")
    assert response.status_code == 410
    assert "MICROSOFT_CLIENT_SECRET" not in response.text
