# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Callback, linking, replay, and configuration endpoint tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api.v1.endpoints import auth as auth_endpoint
from app.core.config import settings
from app.models import ExternalIdentity, ExternalIdentityProvider, User
from app.services import auth as auth_service
from app.services.microsoft_oidc import (
    MicrosoftOidcAccountConflictError,
    MicrosoftOidcExternalIdentityConflictError,
    MicrosoftOidcIdentity,
    link_external_identity,
    resolve_or_create_user,
)
from app.services.oauth_state import OAuthStateTransaction


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


def make_request(*, access_token: str | None = None) -> Request:
    headers = []
    if access_token is not None:
        headers.append((b"cookie", f"zabt_access_token={access_token}".encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/auth/microsoft/callback",
            "headers": headers,
        }
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


def test_link_callback_requires_matching_active_web_session(monkeypatch: pytest.MonkeyPatch):
    user = make_user(id=7)
    db = FakeDb(users={7: user})
    monkeypatch.setattr(auth_endpoint.security, "verify_access_token", lambda token: {"sub": "7"})

    assert auth_endpoint._get_authenticated_link_user(make_request(access_token="access"), db, 7) is user

    monkeypatch.setattr(auth_endpoint.security, "verify_access_token", lambda token: {"sub": "8"})
    assert auth_endpoint._get_authenticated_link_user(make_request(access_token="access"), db, 7) is None


def test_validated_identity_creates_local_user_without_password():
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


class FakeStateService:
    def __init__(self, transaction: OAuthStateTransaction):
        self.transaction = transaction
        self.consumed = 0

    def consume(self, state):
        self.consumed += 1
        if self.consumed > 1:
            return None
        return self.transaction if state == self.transaction.state else None

    def create_transaction(self, **kwargs):
        expected = {
            "purpose": self.transaction.purpose,
            "next_path": "/meetings" if self.transaction.purpose == "oidc_login" else "/integrations",
        }
        if self.transaction.user_id is not None:
            expected["user_id"] = self.transaction.user_id
        assert kwargs == expected
        return self.transaction


class FakeOidcClient:
    def __init__(self, claims=None):
        self.claims = claims or {"sub": "subject-1", "tid": "tenant-1"}
        self.exchange_calls = 0
        self.validation_calls = 0
        self.authorization_args = None

    async def build_authorization_url(self, **kwargs):
        self.authorization_args = kwargs
        return "https://login.example/authorize?state=state"

    async def exchange_code(self, *, code, code_verifier):
        self.exchange_calls += 1
        assert code == "authorization-code"
        assert code_verifier == "v" * 43
        return {"id_token": "validated-id-token"}

    async def validate_id_token(self, token, *, nonce):
        self.validation_calls += 1
        assert token == "validated-id-token"
        assert nonce == "n" * 32
        return self.claims


@dataclass
class CallbackHarness:
    state: FakeStateService
    oidc: FakeOidcClient
    db: FakeDb
    cookies: list[object]


def callback_harness(
    *,
    purpose: str = "oidc_login",
    user_id: int | None = None,
    next_path: str = "/groups/42",
) -> CallbackHarness:
    transaction = OAuthStateTransaction(
        state="state-" + "a" * 32,
        purpose=purpose,
        nonce="n" * 32,
        code_verifier="v" * 43,
        next_path=next_path,
        user_id=user_id,
    )
    return CallbackHarness(
        state=FakeStateService(transaction),
        oidc=FakeOidcClient(),
        db=FakeDb(),
        cookies=[],
    )


@pytest.mark.asyncio
async def test_callback_issues_existing_local_cookie_session_and_replay_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = callback_harness()
    monkeypatch.setattr(auth_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(auth_endpoint, "_microsoft_client", lambda: harness.oidc)
    monkeypatch.setattr(auth_endpoint, "is_microsoft_oidc_configured", lambda: True)
    monkeypatch.setattr(auth_endpoint, "resolve_or_create_user", lambda db, identity: make_user())
    monkeypatch.setattr(
        auth_endpoint.auth_service,
        "issue_tokens",
        lambda db, user: auth_service.TokenBundle("access", "refresh", 900),
    )
    monkeypatch.setattr(
        auth_endpoint.auth_service,
        "set_web_auth_cookies",
        lambda response, tokens: harness.cookies.append(tokens),
    )
    monkeypatch.setattr(settings, "APP_URL", "https://app.example")

    response = await auth_endpoint.microsoft_oidc_callback(
        request=make_request(),
        db=harness.db,
        code="authorization-code",
        state=harness.state.transaction.state,
        error=None,
    )
    replay = await auth_endpoint.microsoft_oidc_callback(
        request=make_request(),
        db=harness.db,
        code="authorization-code",
        state=harness.state.transaction.state,
        error=None,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://app.example/groups/42"
    assert harness.cookies == [auth_service.TokenBundle("access", "refresh", 900)]
    assert replay.headers["location"].endswith("error=microsoft_sign_in_failed")
    assert harness.oidc.exchange_calls == 1


@pytest.mark.asyncio
async def test_callback_anonymous_local_account_conflict_redirects_without_identity_details(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = callback_harness()
    monkeypatch.setattr(auth_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(auth_endpoint, "_microsoft_client", lambda: harness.oidc)
    monkeypatch.setattr(auth_endpoint, "is_microsoft_oidc_configured", lambda: True)
    monkeypatch.setattr(
        auth_endpoint,
        "resolve_or_create_user",
        lambda db, identity: (_ for _ in ()).throw(MicrosoftOidcAccountConflictError()),
    )
    monkeypatch.setattr(settings, "APP_URL", "https://app.example")

    response = await auth_endpoint.microsoft_oidc_callback(
        request=make_request(),
        db=harness.db,
        code="authorization-code",
        state=harness.state.transaction.state,
        error=None,
    )

    location = response.headers["location"]
    assert location == "https://app.example/login?error=microsoft_local_account_exists"
    assert "subject-1" not in location
    assert "user@example.com" not in location
    assert "client-secret" not in location


@pytest.mark.asyncio
async def test_callback_explicit_link_success_redirects_to_integrations_without_new_cookies(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = callback_harness(
        purpose="oidc_link",
        user_id=7,
        next_path="/integrations",
    )
    linked_users = []
    monkeypatch.setattr(auth_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(auth_endpoint, "_microsoft_client", lambda: harness.oidc)
    monkeypatch.setattr(auth_endpoint, "is_microsoft_oidc_configured", lambda: True)
    monkeypatch.setattr(
        auth_endpoint,
        "_get_authenticated_link_user",
        lambda request, db, user_id: make_user(id=user_id),
    )
    monkeypatch.setattr(
        auth_endpoint,
        "link_external_identity",
        lambda db, identity, user: linked_users.append((identity, user)),
    )
    monkeypatch.setattr(settings, "APP_URL", "https://app.example")

    response = await auth_endpoint.microsoft_oidc_callback(
        request=make_request(),
        db=harness.db,
        code="authorization-code",
        state=harness.state.transaction.state,
        error=None,
    )

    assert response.headers["location"] == "https://app.example/integrations?microsoft=linked"
    assert len(linked_users) == 1
    assert harness.cookies == []


@pytest.mark.asyncio
async def test_callback_explicit_link_conflict_is_safe_and_does_not_leak_identity_details(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = callback_harness(
        purpose="oidc_link",
        user_id=7,
        next_path="/integrations",
    )
    monkeypatch.setattr(auth_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(auth_endpoint, "_microsoft_client", lambda: harness.oidc)
    monkeypatch.setattr(auth_endpoint, "is_microsoft_oidc_configured", lambda: True)
    monkeypatch.setattr(
        auth_endpoint,
        "_get_authenticated_link_user",
        lambda request, db, user_id: make_user(id=user_id),
    )
    monkeypatch.setattr(
        auth_endpoint,
        "link_external_identity",
        lambda db, identity, user: (_ for _ in ()).throw(
            MicrosoftOidcExternalIdentityConflictError()
        ),
    )
    monkeypatch.setattr(settings, "APP_URL", "https://app.example")

    response = await auth_endpoint.microsoft_oidc_callback(
        request=make_request(),
        db=harness.db,
        code="authorization-code",
        state=harness.state.transaction.state,
        error=None,
    )

    location = response.headers["location"]
    assert location == "https://app.example/integrations?microsoft=already_linked"
    assert "subject-1" not in location
    assert "client-secret" not in location


@pytest.mark.asyncio
async def test_start_validates_next_path_stores_state_and_redirects_to_provider(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = callback_harness()
    monkeypatch.setattr(auth_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(auth_endpoint, "_microsoft_client", lambda: harness.oidc)
    monkeypatch.setattr(auth_endpoint, "is_microsoft_oidc_configured", lambda: True)

    response = await auth_endpoint.microsoft_oidc_start(next_path="/meetings")

    assert response.status_code == 302
    assert response.headers["location"] == "https://login.example/authorize?state=state"
    assert harness.oidc.authorization_args["state"] == harness.state.transaction.state
    assert harness.oidc.authorization_args["nonce"] == harness.state.transaction.nonce
    with pytest.raises(HTTPException) as invalid:
        await auth_endpoint.microsoft_oidc_start(next_path="https://evil.example")
    assert invalid.value.status_code == 400


@pytest.mark.asyncio
async def test_link_start_requires_active_user_and_stores_link_purpose_and_owner(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = callback_harness(
        purpose="oidc_link",
        user_id=7,
        next_path="/integrations",
    )
    monkeypatch.setattr(auth_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(auth_endpoint, "_microsoft_client", lambda: harness.oidc)
    monkeypatch.setattr(auth_endpoint, "is_microsoft_oidc_configured", lambda: True)

    response = await auth_endpoint.microsoft_oidc_link_start(
        current_user=make_user(id=7),
        next_path="/integrations",
    )

    assert response.status_code == 302
    assert harness.oidc.authorization_args["state"] == harness.state.transaction.state


@pytest.mark.asyncio
async def test_callback_provider_cancel_consumes_state_without_exchanging_code(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = callback_harness()
    monkeypatch.setattr(auth_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(settings, "APP_URL", "https://app.example")

    response = await auth_endpoint.microsoft_oidc_callback(
        request=make_request(),
        db=harness.db,
        state=harness.state.transaction.state,
        error="access_denied",
    )

    assert response.headers["location"].endswith("error=microsoft_cancelled")
    assert harness.oidc.exchange_calls == 0
    assert harness.state.consumed == 1


@pytest.mark.asyncio
async def test_callback_inactive_user_receives_generic_failure_without_cookies(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = callback_harness()
    monkeypatch.setattr(auth_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(auth_endpoint, "_microsoft_client", lambda: harness.oidc)
    monkeypatch.setattr(auth_endpoint, "is_microsoft_oidc_configured", lambda: True)

    def reject_inactive(_db, _identity):
        raise auth_service.InactiveUserError

    monkeypatch.setattr(auth_endpoint, "resolve_or_create_user", reject_inactive)
    monkeypatch.setattr(settings, "APP_URL", "https://app.example")

    response = await auth_endpoint.microsoft_oidc_callback(
        request=make_request(),
        db=harness.db,
        code="authorization-code",
        state=harness.state.transaction.state,
        error=None,
    )

    assert response.headers["location"].endswith("error=microsoft_sign_in_failed")
    assert harness.db.rollbacks == 1
    assert harness.cookies == []


def test_status_endpoint_returns_only_non_sensitive_configuration(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "MICROSOFT_TENANT_ID", "tenant.example")
    monkeypatch.setattr(settings, "MICROSOFT_OIDC_REDIRECT_URI", "https://api.example/oidc")
    monkeypatch.setattr(settings, "MICROSOFT_REDIRECT_URI", "https://api.example/graph")
    monkeypatch.setattr(auth_endpoint, "is_microsoft_oidc_configured", lambda: False)

    test_app = FastAPI()
    test_app.include_router(auth_endpoint.router, prefix="/auth")
    with TestClient(test_app) as client:
        response = client.get("/auth/microsoft/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is False
    assert payload["tenant"] == "tenant.example"
    assert payload["oidc_redirect_uri"] == "https://api.example/oidc"
    assert payload["graph_redirect_uri"] == "https://api.example/graph"
    assert payload["oidc_scopes"] == ["openid", "profile", "email"]
    assert "client_secret" not in payload
