# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Router-level local-auth checks with an isolated SQLite database."""

from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_db
from starlette.websockets import WebSocketDisconnect

from app.api.v1.endpoints import auth, transcriptions, users
from app.core.config import settings
from app.core import security
from app.models import (
    AuthSession,
    AuthenticationConfiguration,
    ExternalIdentity,
    ExternalIdentityProvider,
    MicrosoftOidcConfiguration,
    User,
    UserLoginMode,
)
from app.services.auth import hash_password
from app.services.oauth_state import OAuthStateService


class _ChallengeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    def set(self, key, value, *, ex, nx):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def getdel(self, key):
        return self.values.pop(key, None)


@pytest.fixture()
def auth_database() -> Generator:
    db_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # The production User model uses PostgreSQL JSONB. The auth contract does
    # not depend on that field, so use SQLite's JSON type for this isolated
    # router test and restore the metadata after the fixture.
    json_column = User.__table__.c.language_preferences
    original_type = json_column.type
    json_column.type = JSON()
    try:
        SQLModel.metadata.create_all(
            db_engine,
            tables=[
                User.__table__,
                AuthSession.__table__,
                AuthenticationConfiguration.__table__,
                MicrosoftOidcConfiguration.__table__,
                ExternalIdentity.__table__,
            ],
        )
        yield db_engine
    finally:
        json_column.type = original_type


@pytest.fixture()
def auth_client(
    auth_database,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    db_engine = auth_database

    def get_test_db():
        with Session(db_engine) as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(auth.router, prefix="/auth")
    test_app.include_router(users.router, prefix="/users")
    test_app.dependency_overrides[get_db] = get_test_db
    monkeypatch.setattr(settings, "AUTH_ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", False)

    with TestClient(test_app) as client:
        yield client


def test_web_cookie_contract_and_csrf_origin_check(auth_client: TestClient):
    blocked = auth_client.post(
        "/auth/register",
        json={
            "email": "blocked@example.com",
            "password": "correct horse battery staple",
            "client": "web",
        },
    )
    assert blocked.status_code == 403

    registered = auth_client.post(
        "/auth/register",
        headers={"Origin": "http://localhost:3000"},
        json={
            "email": "web@example.com",
            "password": "correct horse battery staple",
            "client": "web",
        },
    )
    assert registered.status_code == 201
    assert registered.json()["access_token"] is None
    assert registered.json()["refresh_token"] is None
    assert registered.json()["user"]["is_superuser"] is False
    assert "HttpOnly" in registered.headers["set-cookie"]
    assert "SameSite=lax" in registered.headers["set-cookie"]
    assert "Secure" not in registered.headers["set-cookie"]

    me = auth_client.get("/users/me")
    assert me.status_code == 200
    assert me.json()["email"] == "web@example.com"


def test_mobile_tokens_refresh_once_and_logout_revokes_session(
    auth_client: TestClient,
    auth_database,
):
    login = auth_client.post(
        "/auth/register",
        json={
            "email": "mobile@example.com",
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )
    assert login.status_code == 201
    token_data = login.json()
    assert token_data["access_token"]
    assert token_data["refresh_token"]

    with Session(auth_database) as db:
        session = db.exec(select(AuthSession)).one()
        assert session.refresh_token_hash != token_data["refresh_token"]
        assert len(session.refresh_token_hash) == 64

    me = auth_client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    )
    assert me.status_code == 200

    refreshed = auth_client.post(
        "/auth/refresh",
        json={"client": "mobile", "refresh_token": token_data["refresh_token"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != token_data["refresh_token"]

    replay = auth_client.post(
        "/auth/refresh",
        json={"client": "mobile", "refresh_token": token_data["refresh_token"]},
    )
    assert replay.status_code == 401

    logout = auth_client.post(
        "/auth/logout",
        json={"client": "mobile", "refresh_token": refreshed.json()["refresh_token"]},
    )
    assert logout.status_code == 200


def test_login_rejects_invalid_and_inactive_accounts(
    auth_client: TestClient,
    auth_database,
):
    registered = auth_client.post(
        "/auth/register",
        json={
            "email": "inactive@example.com",
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )
    invalid = auth_client.post(
        "/auth/login",
        json={
            "email": "inactive@example.com",
            "password": "wrong password",
            "client": "mobile",
        },
    )
    assert invalid.status_code == 401

    with Session(auth_database) as db:
        user = db.exec(
            select(User).where(User.email == "inactive@example.com")
        ).one()
        user.is_active = False
        db.add(user)
        db.commit()

    inactive = auth_client.post(
        "/auth/login",
        json={
            "email": "inactive@example.com",
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )
    assert inactive.status_code == 403

    access_after_deactivation = auth_client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {registered.json()['access_token']}"},
    )
    assert access_after_deactivation.status_code == 403


def _create_account(
    database,
    *,
    email: str,
    superuser: bool = False,
    legacy_admin: bool = False,
    password: str | None = "correct horse battery staple",
) -> User:
    with Session(database) as db:
        user = User(
            email=email,
            password_hash=hash_password(password) if password is not None else None,
            is_active=True,
            is_superuser=superuser,
            is_admin=legacy_admin,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _set_mode(database, mode: UserLoginMode, *, updated_by: int | None = None) -> None:
    with Session(database) as db:
        db.add(
            AuthenticationConfiguration(
                id=1,
                user_login_mode=mode,
                updated_by=updated_by,
            )
        )
        db.commit()


def _add_oidc_configuration(database) -> None:
    with Session(database) as db:
        db.add(
            MicrosoftOidcConfiguration(
                client_id="11111111-1111-4111-8111-111111111111",
                tenant_id="common",
                redirect_uri="http://localhost:3000/login",
                enabled=True,
            )
        )
        db.commit()


def _test_oidc_state_service() -> OAuthStateService:
    return OAuthStateService(_ChallengeRedis())


def _superuser_login(client: TestClient, email: str) -> None:
    response = client.post(
        "/auth/superuser/login",
        headers={"Origin": "http://localhost:3000"},
        json={"email": email, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] is None


def test_authentication_mode_is_public_and_defaults_to_local(auth_client: TestClient):
    response = auth_client.get("/auth/mode")

    assert response.status_code == 200
    assert response.json() == {"mode": "local", "oidc_configured": False}
    assert response.headers["cache-control"] == "no-store"


def test_oidc_login_challenge_is_mode_gated_and_stores_no_user(
    auth_client: TestClient,
    auth_database,
    monkeypatch: pytest.MonkeyPatch,
):
    state_service = _test_oidc_state_service()
    monkeypatch.setattr(auth, "oauth_state_service", state_service)
    headers = {"Origin": "http://localhost:3000"}

    blocked = auth_client.post(
        "/auth/microsoft/oidc/challenge",
        headers=headers,
        json={"purpose": "login", "client": "web"},
    )
    assert blocked.status_code == 403

    _add_oidc_configuration(auth_database)
    _set_mode(auth_database, UserLoginMode.MICROSOFT_OIDC)
    auth_client.cookies.set(settings.AUTH_ACCESS_COOKIE_NAME, "expired-access-cookie")
    web_challenge = auth_client.post(
        "/auth/microsoft/oidc/challenge",
        headers=headers,
        json={"purpose": "login", "client": "web"},
    )
    auth_client.cookies.clear()
    mobile_challenge = auth_client.post(
        "/auth/microsoft/oidc/challenge",
        json={"purpose": "login", "client": "mobile"},
    )

    assert web_challenge.status_code == mobile_challenge.status_code == 200
    assert set(web_challenge.json()) == {"challenge_id", "nonce"}
    assert web_challenge.headers["cache-control"] == "no-store"
    web_transaction = state_service.consume(web_challenge.json()["challenge_id"])
    mobile_transaction = state_service.consume(mobile_challenge.json()["challenge_id"])
    assert web_transaction is not None
    assert web_transaction.purpose == "oidc_login"
    assert web_transaction.client == "web"
    assert web_transaction.user_id is None
    assert web_transaction.nonce == web_challenge.json()["nonce"]
    assert mobile_transaction is not None
    assert mobile_transaction.client == "mobile"
    assert mobile_transaction.user_id is None


def test_oidc_link_challenge_is_user_bound_and_allowed_in_local_mode(
    auth_client: TestClient,
    auth_database,
    monkeypatch: pytest.MonkeyPatch,
):
    user = _create_account(auth_database, email="linkable@example.com")
    login = auth_client.post(
        "/auth/login",
        headers={"Origin": "http://localhost:3000"},
        json={
            "email": user.email,
            "password": "correct horse battery staple",
            "client": "web",
        },
    )
    assert login.status_code == 200
    _add_oidc_configuration(auth_database)
    state_service = _test_oidc_state_service()
    monkeypatch.setattr(auth, "oauth_state_service", state_service)

    link_challenge = auth_client.post(
        "/auth/microsoft/oidc/challenge",
        headers={"Origin": "http://localhost:3000"},
        json={"purpose": "link", "client": "web"},
    )

    assert link_challenge.status_code == 200
    transaction = state_service.consume(link_challenge.json()["challenge_id"])
    assert transaction is not None
    assert transaction.purpose == "oidc_link"
    assert transaction.client == "web"
    assert transaction.user_id == user.id


def test_oidc_mode_only_issues_link_challenge_to_already_linked_user(
    auth_client: TestClient,
    auth_database,
    monkeypatch: pytest.MonkeyPatch,
):
    user = _create_account(auth_database, email="linked@example.com")
    auth_client.post(
        "/auth/login",
        headers={"Origin": "http://localhost:3000"},
        json={
            "email": user.email,
            "password": "correct horse battery staple",
            "client": "web",
        },
    )
    _add_oidc_configuration(auth_database)
    _set_mode(auth_database, UserLoginMode.MICROSOFT_OIDC)
    state_service = _test_oidc_state_service()
    monkeypatch.setattr(auth, "oauth_state_service", state_service)

    payload = {"purpose": "link", "client": "web"}
    headers = {"Origin": "http://localhost:3000"}
    unlinked = auth_client.post(
        "/auth/microsoft/oidc/challenge",
        headers=headers,
        json=payload,
    )
    assert unlinked.status_code == 403

    with Session(auth_database) as db:
        db.add(
            ExternalIdentity(
                user_id=user.id,
                provider=ExternalIdentityProvider.MICROSOFT,
                tenant_id="tenant-1",
                subject="linked-subject",
                email=user.email,
            )
        )
        db.commit()

    linked = auth_client.post(
        "/auth/microsoft/oidc/challenge",
        headers=headers,
        json=payload,
    )
    assert linked.status_code == 200
    transaction = state_service.consume(linked.json()["challenge_id"])
    assert transaction is not None
    assert transaction.user_id == user.id


def test_authentication_mode_configuration_is_superuser_only_and_reports_readiness(
    auth_client: TestClient,
    auth_database,
):
    ordinary = _create_account(
        auth_database,
        email="ordinary@example.com",
        legacy_admin=True,
    )
    operator = _create_account(auth_database, email="operator@example.com", superuser=True)
    ordinary_login = auth_client.post(
        "/auth/login",
        json={
            "email": ordinary.email,
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )
    denied = auth_client.get(
        "/auth/mode/config",
        headers={"Authorization": f"Bearer {ordinary_login.json()['access_token']}"},
    )
    assert denied.status_code == 403

    _superuser_login(auth_client, operator.email)
    configuration = auth_client.get("/auth/mode/config")

    assert configuration.status_code == 200
    assert configuration.json() == {
        "mode": "local",
        "oidc_configured": False,
        "unlinked_user_count": 1,
        "unlinked_passwordless_user_count": 0,
    }
    assert configuration.headers["cache-control"] == "no-store"


def test_local_registration_never_creates_a_superuser(
    auth_client: TestClient,
    auth_database,
):
    registered = auth_client.post(
        "/auth/register",
        json={
            "email": "ordinary@example.com",
            "password": "correct horse battery staple",
            "client": "mobile",
            "is_superuser": True,
            "is_admin": True,
        },
    )

    assert registered.status_code == 201
    with Session(auth_database) as db:
        user = db.exec(select(User).where(User.email == "ordinary@example.com")).one()
    assert user.is_superuser is False
    assert user.is_admin is False


def test_regular_local_login_is_allowed_in_local_mode(auth_client: TestClient):
    auth_client.post(
        "/auth/register",
        json={
            "email": "local@example.com",
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )

    login = auth_client.post(
        "/auth/login",
        json={
            "email": "local@example.com",
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )

    assert login.status_code == 200
    assert login.json()["access_token"]
    assert login.json()["user"]["is_superuser"] is False


def test_local_login_and_registration_are_blocked_in_oidc_mode(
    auth_client: TestClient,
    auth_database,
):
    _set_mode(auth_database, UserLoginMode.MICROSOFT_OIDC)

    login = auth_client.post(
        "/auth/login",
        json={
            "email": "unknown@example.com",
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )
    register = auth_client.post(
        "/auth/register",
        json={
            "email": "not-created@example.com",
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )

    assert login.status_code == 403
    assert register.status_code == 403
    assert login.json() == register.json()
    with Session(auth_database) as db:
        assert db.exec(select(User)).all() == []


@pytest.mark.parametrize("mode", [UserLoginMode.LOCAL, UserLoginMode.MICROSOFT_OIDC])
def test_superuser_local_login_works_in_either_mode_and_rejects_ordinary_users(
    auth_client: TestClient,
    auth_database,
    mode: UserLoginMode,
):
    _set_mode(auth_database, mode)
    _create_account(auth_database, email="operator@example.com", superuser=True)
    _create_account(auth_database, email="ordinary@example.com")

    _superuser_login(auth_client, "operator@example.com")
    denied = auth_client.post(
        "/auth/superuser/login",
        headers={"Origin": "http://localhost:3000"},
        json={"email": "ordinary@example.com", "password": "correct horse battery staple"},
    )

    assert denied.status_code == 401
    assert denied.json()["detail"] == "Invalid email or password"


def test_superuser_cannot_use_regular_local_login(
    auth_client: TestClient,
    auth_database,
):
    _create_account(auth_database, email="operator@example.com", superuser=True)

    denied = auth_client.post(
        "/auth/login",
        json={
            "email": "operator@example.com",
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )

    assert denied.status_code == 403
    assert denied.json()["detail"] == "Use the system superuser sign-in endpoint"


def test_legacy_admin_cannot_change_global_authentication_mode(
    auth_client: TestClient,
    auth_database,
):
    ordinary = _create_account(
        auth_database,
        email="ordinary@example.com",
        legacy_admin=True,
    )
    login = auth_client.post(
        "/auth/login",
        headers={"Origin": "http://localhost:3000"},
        json={"email": ordinary.email, "password": "correct horse battery staple", "client": "web"},
    )
    assert login.status_code == 200

    response = auth_client.put(
        "/auth/mode/config",
        headers={"Origin": "http://localhost:3000"},
        json={"mode": "microsoft_oidc"},
    )

    assert ordinary.is_admin is True
    assert ordinary.is_superuser is False
    assert response.status_code == 403
    assert response.json()["detail"] == "System superuser access is required"


def test_oidc_mode_requires_ready_configuration_and_linked_local_accounts(
    auth_client: TestClient,
    auth_database,
):
    operator = _create_account(auth_database, email="operator@example.com", superuser=True)
    local_user = _create_account(auth_database, email="local@example.com")
    linked_user = _create_account(auth_database, email="linked@example.com")
    inactive_local = _create_account(auth_database, email="inactive@example.com")
    with Session(auth_database) as db:
        inactive_local.is_active = False
        db.add(inactive_local)
        db.commit()
    _superuser_login(auth_client, operator.email)

    missing_configuration = auth_client.put(
        "/auth/mode/config",
        headers={"Origin": "http://localhost:3000"},
        json={"mode": "microsoft_oidc"},
    )
    assert missing_configuration.status_code == 409
    assert "fully configured and enabled" in missing_configuration.json()["detail"]

    _add_oidc_configuration(auth_database)
    missing_link = auth_client.put(
        "/auth/mode/config",
        headers={"Origin": "http://localhost:3000"},
        json={"mode": "microsoft_oidc"},
    )
    assert missing_link.status_code == 409
    assert "2 active local accounts" in missing_link.json()["detail"]

    with Session(auth_database) as db:
        db.add(
            ExternalIdentity(
                user_id=linked_user.id,
                provider=ExternalIdentityProvider.MICROSOFT,
                tenant_id="tenant-1",
                subject="subject-linked",
                email=linked_user.email,
            )
        )
        db.commit()

    readiness = auth_client.get("/auth/mode/config")
    assert readiness.status_code == 200
    assert readiness.json() == {
        "mode": "local",
        "oidc_configured": True,
        "unlinked_user_count": 1,
        "unlinked_passwordless_user_count": 0,
    }

    still_missing_link = auth_client.put(
        "/auth/mode/config",
        headers={"Origin": "http://localhost:3000"},
        json={"mode": "microsoft_oidc"},
    )
    assert still_missing_link.status_code == 409
    assert "1 active local account" in still_missing_link.json()["detail"]

    with Session(auth_database) as db:
        db.add(
            ExternalIdentity(
                user_id=local_user.id,
                provider=ExternalIdentityProvider.MICROSOFT,
                tenant_id="tenant-1",
                subject="subject-local",
                email=local_user.email,
            )
        )
        db.commit()

    switched = auth_client.put(
        "/auth/mode/config",
        headers={"Origin": "http://localhost:3000"},
        json={"mode": "microsoft_oidc"},
    )

    assert switched.status_code == 200
    assert switched.json() == {
        "mode": "microsoft_oidc",
        "oidc_configured": True,
        "unlinked_user_count": 0,
        "unlinked_passwordless_user_count": 0,
    }


def test_active_passwordless_unlinked_account_blocks_oidc_mode_switch(
    auth_client: TestClient,
    auth_database,
):
    operator = _create_account(auth_database, email="operator@example.com", superuser=True)
    _create_account(auth_database, email="legacy@example.com", password=None)
    _add_oidc_configuration(auth_database)
    _superuser_login(auth_client, operator.email)

    readiness = auth_client.get("/auth/mode/config")
    assert readiness.status_code == 200
    assert readiness.json()["unlinked_user_count"] == 1
    assert readiness.json()["unlinked_passwordless_user_count"] == 1

    blocked = auth_client.put(
        "/auth/mode/config",
        headers={"Origin": "http://localhost:3000"},
        json={"mode": "microsoft_oidc"},
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == (
        "Active passwordless accounts without a linked Microsoft identity require identity "
        "reconciliation or deactivation by the system superuser before switching to Microsoft OIDC."
    )
    assert "sign in locally" not in blocked.json()["detail"].lower()
    assert auth_client.get("/auth/mode/config").json()["mode"] == "local"


def test_passwordless_account_linked_to_microsoft_does_not_block_oidc_mode_switch(
    auth_client: TestClient,
    auth_database,
):
    operator = _create_account(auth_database, email="operator@example.com", superuser=True)
    passwordless_user = _create_account(
        auth_database,
        email="linked-legacy@example.com",
        password=None,
    )
    with Session(auth_database) as db:
        db.add(
            ExternalIdentity(
                user_id=passwordless_user.id,
                provider=ExternalIdentityProvider.MICROSOFT,
                tenant_id="tenant-1",
                subject="linked-passwordless-subject",
                email=passwordless_user.email,
            )
        )
        db.commit()
    _add_oidc_configuration(auth_database)
    _superuser_login(auth_client, operator.email)

    readiness = auth_client.get("/auth/mode/config")
    assert readiness.status_code == 200
    assert readiness.json()["unlinked_user_count"] == 0
    assert readiness.json()["unlinked_passwordless_user_count"] == 0

    switched = auth_client.put(
        "/auth/mode/config",
        headers={"Origin": "http://localhost:3000"},
        json={"mode": "microsoft_oidc"},
    )

    assert switched.status_code == 200
    assert switched.json()["mode"] == "microsoft_oidc"


def test_active_oidc_can_be_rolled_back_to_local_but_not_disabled_in_place(
    auth_client: TestClient,
    auth_database,
):
    operator = _create_account(auth_database, email="operator@example.com", superuser=True)
    _add_oidc_configuration(auth_database)
    _set_mode(auth_database, UserLoginMode.MICROSOFT_OIDC, updated_by=operator.id)
    _superuser_login(auth_client, operator.email)

    changed_tenant = auth_client.put(
        "/auth/microsoft/config",
        headers={"Origin": "http://localhost:3000"},
        json={
            "client_id": "11111111-1111-4111-8111-111111111111",
            "tenant": "organizations",
            "redirect_uri": "http://localhost:3000/login",
            "enabled": True,
        },
    )
    assert changed_tenant.status_code == 409

    disabled = auth_client.put(
        "/auth/microsoft/config",
        headers={"Origin": "http://localhost:3000"},
        json={
            "client_id": "11111111-1111-4111-8111-111111111111",
            "tenant": "common",
            "redirect_uri": "http://localhost:3000/login",
            "enabled": False,
        },
    )
    assert disabled.status_code == 409

    rollback = auth_client.put(
        "/auth/mode/config",
        headers={"Origin": "http://localhost:3000"},
        json={"mode": "local"},
    )
    assert rollback.status_code == 200
    assert rollback.json()["mode"] == "local"


class _FakeUserSession:
    def __init__(self, user: User):
        self.user = user

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def get(self, model, user_id):
        return self.user if model is User and self.user.id == user_id else None


@pytest.fixture()
def websocket_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    websocket_app = FastAPI()
    websocket_app.include_router(transcriptions.router)
    monkeypatch.setattr(settings, "AUTH_ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(transcriptions, "get_provider", lambda: None)
    with TestClient(websocket_app) as client:
        yield client


def _rejecting_websocket(websocket_client: TestClient, url: str, **headers: str) -> int:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with websocket_client.websocket_connect(url, headers=headers) as websocket:
            websocket.receive_text()
    return exc_info.value.code


def test_websocket_query_token_requires_allowed_origin(websocket_client: TestClient):
    token, _ = security.create_access_token(42)

    assert _rejecting_websocket(websocket_client, f"/ws/1?token={token}") == 1008


def test_websocket_rejects_inactive_local_user(
    websocket_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    user = User(id=42, email="inactive-ws@example.com", is_active=False)
    monkeypatch.setattr(
        transcriptions,
        "Session",
        lambda _engine: _FakeUserSession(user),
    )
    monkeypatch.setattr(
        transcriptions.meeting_service,
        "get_meeting",
        lambda _meeting_id: SimpleNamespace(owner_id=42),
    )
    token, _ = security.create_access_token(user.id)

    assert (
        _rejecting_websocket(
            websocket_client,
            f"/ws/1?token={token}",
            Origin="http://localhost:3000",
        )
        == 1008
    )


def test_websocket_keeps_meeting_ownership_enforcement(
    websocket_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    user = User(id=42, email="owner-ws@example.com", is_active=True)
    monkeypatch.setattr(
        transcriptions,
        "Session",
        lambda _engine: _FakeUserSession(user),
    )
    monkeypatch.setattr(
        transcriptions.meeting_service,
        "get_meeting",
        lambda _meeting_id: SimpleNamespace(owner_id=99),
    )
    token, _ = security.create_access_token(user.id)

    assert (
        _rejecting_websocket(
            websocket_client,
            f"/ws/1?token={token}",
            Origin="http://localhost:3000",
        )
        == 1008
    )
