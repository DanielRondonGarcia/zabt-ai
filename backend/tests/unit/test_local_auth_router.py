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
from app.models import AuthSession, User


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
            tables=[User.__table__, AuthSession.__table__],
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
