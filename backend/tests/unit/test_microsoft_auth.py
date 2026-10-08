# SPDX-License-Identifier: AGPL-3.0-only
"""Public OIDC endpoint, account-resolution, cookie, and admin tests."""

from __future__ import annotations

from datetime import datetime
import json

import pytest
from fastapi import HTTPException, Request, Response
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.api.v1.endpoints import auth as auth_endpoint
from app.core.config import settings
from app.models import (
    ExternalIdentity,
    ExternalIdentityProvider,
    MicrosoftOidcConfiguration,
    User,
    UserLoginMode,
)
from app.services import auth as auth_service
from app.services.microsoft_oidc import (
    MicrosoftOidcAccountConflictError,
    MicrosoftOidcExternalIdentityConflictError,
    MicrosoftOidcIdentity,
    link_external_identity,
    resolve_or_create_user,
)
from app.services.oauth_state import OAuthStateTransaction


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


def make_request(
    id_token: str | None = None,
    *,
    client: str | None = "web",
    challenge_id: str | None = "c" * 43,
    origin: str | None = "http://localhost:3001",
) -> Request:
    if id_token is None:
        body = b""
    else:
        payload = {"id_token": id_token}
        if client is not None:
            payload["client"] = client
        if challenge_id is not None:
            payload["challenge_id"] = challenge_id
        body = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [(b"content-length", str(len(body)).encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/microsoft/oidc/exchange",
            "headers": headers,
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
    admin = make_user(is_admin=False, is_superuser=True)
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
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.LOCAL,
    )

    get_http_response = Response()
    get_response = auth_endpoint.microsoft_oidc_configuration(get_http_response, admin, db)
    assert get_response.can_manage is True
    assert get_response.is_admin is False
    assert get_response.is_superuser is True
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


def test_non_superuser_cannot_claim_global_configuration_when_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    user = make_user(is_admin=True)
    monkeypatch.setattr(
        auth_endpoint.security,
        "validate_request_origin",
        lambda request: "http://localhost:3001",
    )

    with pytest.raises(HTTPException) as view_error:
        auth_endpoint.microsoft_oidc_configuration(
            Response(),
            user,
            FakeDb(exec_rows=[[], []]),
        )
    assert view_error.value.status_code == 403

    db = FakeDb(exec_rows=[[]])
    with pytest.raises(HTTPException) as error:
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
    assert user.is_admin is True
    assert db.commits == 0


def test_non_superuser_cannot_overwrite_after_configuration_exists(
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

    with pytest.raises(HTTPException) as error:
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


def test_non_superuser_cannot_read_existing_configuration():
    user = make_user(is_admin=False)
    response = Response()

    with pytest.raises(HTTPException) as error:
        auth_endpoint.microsoft_oidc_configuration(
            response,
            user,
            FakeDb(exec_rows=[]),
        )

    assert error.value.status_code == 403
    assert response.headers["cache-control"] == "no-store"


class FakeVerifier:
    def __init__(self, claims):
        self.claims = claims
        self.nonces = []

    async def validate_id_token(self, id_token: str, *, nonce: str | None = None):
        assert id_token == "signed-id-token"
        self.nonces.append(nonce)
        return self.claims


def install_challenge(
    monkeypatch: pytest.MonkeyPatch,
    *,
    purpose: str = "oidc_login",
    client: str = "web",
    user_id: int | None = None,
    nonce: str = "server-issued-nonce-value",
    challenge_id: str = "c" * 43,
):
    transaction = OAuthStateTransaction(
        state=challenge_id,
        purpose=purpose,
        nonce=nonce,
        code_verifier="v" * 43,
        next_path="/",
        user_id=user_id,
        client=client,
    )

    class OneTimeChallenge:
        def __init__(self):
            self.transaction = transaction
            self.consumed = []

        def consume(self, state):
            self.consumed.append(state)
            if state != transaction.state:
                return None
            result = self.transaction
            self.transaction = None
            return result

    service = OneTimeChallenge()
    monkeypatch.setattr(auth_endpoint, "oauth_state_service", service)
    return service, transaction


@pytest.mark.asyncio
async def test_id_token_exchange_issues_existing_web_cookies_without_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = make_configuration()
    user = make_user()
    db = FakeDb(exec_rows=[[configuration]])
    response = Response()
    _, challenge = install_challenge(monkeypatch)
    monkeypatch.setattr(auth_endpoint.security, "validate_request_origin", lambda request: None)
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.MICROSOFT_OIDC,
    )
    verifier = FakeVerifier({
        "sub": "subject-1",
        "tid": "tenant-1",
    })
    monkeypatch.setattr(auth_endpoint, "_oidc_verifier", lambda config: verifier)
    monkeypatch.setattr(auth_endpoint, "resolve_or_create_user", lambda db, identity: user)
    monkeypatch.setattr(
        auth_endpoint.auth_service,
        "issue_tokens",
        lambda db, user: auth_service.TokenBundle("access", "refresh", 900),
    )

    result = await auth_endpoint.exchange_microsoft_oidc_token(
        make_request("signed-id-token", client=None),
        response,
        db,
    )

    assert result.user.id == user.id
    assert verifier.nonces == [challenge.nonce]
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
    install_challenge(monkeypatch)
    monkeypatch.setattr(auth_endpoint.security, "validate_request_origin", lambda request: None)
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.MICROSOFT_OIDC,
    )
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
    _, challenge = install_challenge(
        monkeypatch,
        purpose="oidc_link",
        user_id=user.id,
    )
    monkeypatch.setattr(auth_endpoint.security, "validate_request_origin", lambda request: None)
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.LOCAL,
    )
    verifier = FakeVerifier({
        "sub": "subject-1",
        "tid": "tenant-1",
    })
    monkeypatch.setattr(auth_endpoint, "_oidc_verifier", lambda config: verifier)
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
    assert verifier.nonces == [challenge.nonce]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_oidc_exchange_returns_bearer_tokens_for_mobile_client(
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = make_configuration()
    user = make_user()
    db = FakeDb(exec_rows=[[configuration]])
    response = Response()
    _, challenge = install_challenge(monkeypatch, client="mobile")
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.MICROSOFT_OIDC,
    )
    verifier = FakeVerifier({
        "sub": "subject-1",
        "tid": "tenant-1",
    })
    monkeypatch.setattr(auth_endpoint, "_oidc_verifier", lambda config: verifier)
    monkeypatch.setattr(auth_endpoint, "resolve_or_create_user", lambda db, identity: user)
    monkeypatch.setattr(
        auth_endpoint.auth_service,
        "issue_tokens",
        lambda db, user: auth_service.TokenBundle("access", "refresh", 900),
    )

    result = await auth_endpoint.exchange_microsoft_oidc_token(
        make_request("signed-id-token", client="mobile", origin=None),
        response,
        db,
    )

    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    assert result.user.id == user.id
    assert verifier.nonces == [challenge.nonce]
    assert not any(name.lower() == b"set-cookie" for name, _ in response.raw_headers)


@pytest.mark.asyncio
async def test_oidc_exchange_does_not_authenticate_system_superuser(
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = make_configuration()
    user = make_user(is_superuser=True)
    db = FakeDb(exec_rows=[[configuration]])
    install_challenge(monkeypatch, client="mobile")
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.MICROSOFT_OIDC,
    )
    monkeypatch.setattr(auth_endpoint, "_oidc_verifier", lambda config: FakeVerifier({
        "sub": "subject-1",
        "tid": "tenant-1",
    }))
    monkeypatch.setattr(auth_endpoint, "resolve_or_create_user", lambda db, identity: user)
    monkeypatch.setattr(
        auth_endpoint.auth_service,
        "issue_tokens",
        lambda *args: pytest.fail("superuser must use local superuser login"),
    )

    with pytest.raises(Exception) as error:
        await auth_endpoint.exchange_microsoft_oidc_token(
            make_request("signed-id-token", client="mobile", origin=None),
            Response(),
            db,
        )

    assert error.value.status_code == 403
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_oidc_exchange_requires_challenge_id(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.MICROSOFT_OIDC,
    )

    with pytest.raises(HTTPException) as error:
        await auth_endpoint.exchange_microsoft_oidc_token(
            make_request("signed-id-token", challenge_id=None),
            Response(),
            FakeDb(),
        )

    assert error.value.status_code == 400
    assert "signed-id-token" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("purpose", "stored_client", "request_client", "owner_id"),
    [
        ("oidc_link", "web", "web", 7),
        ("oidc_login", "mobile", "web", None),
    ],
)
async def test_oidc_exchange_consumes_and_rejects_mismatched_challenges(
    monkeypatch: pytest.MonkeyPatch,
    purpose: str,
    stored_client: str,
    request_client: str,
    owner_id: int | None,
):
    configuration = make_configuration()
    service, _ = install_challenge(
        monkeypatch,
        purpose=purpose,
        client=stored_client,
        user_id=owner_id,
    )
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.MICROSOFT_OIDC,
    )
    monkeypatch.setattr(auth_endpoint.security, "validate_request_origin", lambda request: None)
    verifier = FakeVerifier({"sub": "subject-1", "tid": "tenant-1"})
    monkeypatch.setattr(auth_endpoint, "_oidc_verifier", lambda _configuration: verifier)

    with pytest.raises(HTTPException) as error:
        await auth_endpoint.exchange_microsoft_oidc_token(
            make_request("signed-id-token", client=request_client),
            Response(),
            FakeDb(exec_rows=[[configuration]]),
        )

    assert error.value.status_code == 400
    assert service.consumed == ["c" * 43]
    assert verifier.nonces == []


@pytest.mark.asyncio
async def test_oidc_challenge_replay_is_rejected_before_second_token_validation(
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = make_configuration()
    user = make_user()
    service, challenge = install_challenge(monkeypatch)
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.MICROSOFT_OIDC,
    )
    monkeypatch.setattr(auth_endpoint.security, "validate_request_origin", lambda request: None)
    verifier = FakeVerifier({"sub": "subject-1", "tid": "tenant-1"})
    monkeypatch.setattr(auth_endpoint, "_oidc_verifier", lambda _configuration: verifier)
    monkeypatch.setattr(auth_endpoint, "resolve_or_create_user", lambda _db, _identity: user)
    monkeypatch.setattr(
        auth_endpoint.auth_service,
        "issue_tokens",
        lambda _db, _user: auth_service.TokenBundle("access", "refresh", 900),
    )
    db = FakeDb(exec_rows=[[configuration], [configuration]])

    first = await auth_endpoint.exchange_microsoft_oidc_token(
        make_request("signed-id-token"),
        Response(),
        db,
    )
    with pytest.raises(HTTPException) as replay:
        await auth_endpoint.exchange_microsoft_oidc_token(
            make_request("signed-id-token"),
            Response(),
            db,
        )

    assert first.user.id == user.id
    assert replay.value.status_code == 400
    assert verifier.nonces == [challenge.nonce]
    assert service.consumed == [challenge.state, challenge.state]


@pytest.mark.asyncio
async def test_oidc_link_rejects_foreign_user_challenge_before_token_validation(
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = make_configuration()
    user = make_user()
    service, challenge = install_challenge(
        monkeypatch,
        purpose="oidc_link",
        user_id=user.id + 1,
    )
    monkeypatch.setattr(auth_endpoint.security, "validate_request_origin", lambda request: None)
    verifier = FakeVerifier({"sub": "subject-1", "tid": "tenant-1"})
    monkeypatch.setattr(auth_endpoint, "_oidc_verifier", lambda _configuration: verifier)

    with pytest.raises(HTTPException) as error:
        await auth_endpoint.link_microsoft_oidc_token(
            make_request("signed-id-token"),
            user,
            FakeDb(exec_rows=[[configuration]]),
        )

    assert error.value.status_code == 400
    assert service.consumed == [challenge.state]
    assert verifier.nonces == []


@pytest.mark.asyncio
async def test_oidc_link_requires_challenge_id(
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = make_configuration()
    user = make_user()
    monkeypatch.setattr(auth_endpoint.security, "validate_request_origin", lambda request: None)

    with pytest.raises(HTTPException) as error:
        await auth_endpoint.link_microsoft_oidc_token(
            make_request("signed-id-token", challenge_id=None),
            user,
            FakeDb(exec_rows=[[configuration]]),
        )

    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_oidc_exchange_is_rejected_while_local_mode_is_selected(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.LOCAL,
    )
    with pytest.raises(Exception) as error:
        await auth_endpoint.exchange_microsoft_oidc_token(
            make_request("signed-id-token", client="mobile"),
            Response(),
            FakeDb(),
        )

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_oidc_exchange_rejects_unsupported_client(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.MICROSOFT_OIDC,
    )

    with pytest.raises(Exception) as error:
        await auth_endpoint.exchange_microsoft_oidc_token(
            make_request("signed-id-token", client="desktop"),
            Response(),
            FakeDb(),
        )

    assert error.value.status_code == 400
    assert "signed-id-token" not in str(error.value)


def test_id_token_request_bounds_input_without_echoing_value(
    monkeypatch: pytest.MonkeyPatch,
):
    marker = "TOKEN_MARKER"
    monkeypatch.setattr(auth_endpoint.settings, "AUTH_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(
        auth_endpoint.auth_mode_service,
        "get_user_login_mode",
        lambda db: UserLoginMode.MICROSOFT_OIDC,
    )
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
