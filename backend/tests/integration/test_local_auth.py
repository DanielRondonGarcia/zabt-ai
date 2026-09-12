# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""API contract tests for local registration and revocable sessions."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.main import app
from app.models import AuthSession, Meeting, User


@pytest.fixture(autouse=True)
def use_real_auth_dependency():
    """The legacy suite's mock auth fixture must not bypass these tests."""

    previous = app.dependency_overrides.pop(get_current_user, None)
    yield
    if previous is not None:
        app.dependency_overrides[get_current_user] = previous


def _email() -> str:
    return f"local-auth-{uuid4().hex}@example.com"


def test_register_login_me_refresh_and_logout_revoke_session(
    client: TestClient,
    db: Session,
):
    email = _email()
    origin = {"Origin": "http://localhost:3000"}

    register_response = client.post(
        "/api/v1/auth/register",
        headers=origin,
        json={
            "email": email,
            "password": "correct horse battery staple",
            "full_name": "Local Auth",
            "client": "web",
        },
    )
    assert register_response.status_code == 201, register_response.text
    assert register_response.json()["access_token"] is None
    assert "password_hash" not in register_response.json()["user"]
    assert "zabt_access_token" in register_response.cookies
    assert "HttpOnly" in register_response.headers["set-cookie"]

    me_response = client.get("/api/v1/users/me")
    assert me_response.status_code == 200, me_response.text
    assert me_response.json()["email"] == email

    duplicate_response = client.post(
        "/api/v1/auth/register",
        headers=origin,
        json={"email": email.upper(), "password": "another password", "client": "web"},
    )
    assert duplicate_response.status_code == 409

    logout_response = client.post(
        "/api/v1/auth/logout",
        headers=origin,
        json={"client": "web"},
    )
    assert logout_response.status_code == 200
    assert client.get("/api/v1/users/me").status_code == 401

    user = db.exec(select(User).where(User.email == email)).one()
    sessions = db.exec(select(AuthSession).where(AuthSession.user_id == user.id)).all()
    assert sessions
    assert all(session.revoked_at is not None for session in sessions)

    login_response = client.post(
        "/api/v1/auth/login",
        headers=origin,
        json={"email": email, "password": "correct horse battery staple", "client": "mobile"},
    )
    assert login_response.status_code == 200
    mobile_tokens = login_response.json()
    assert mobile_tokens["access_token"]
    assert mobile_tokens["refresh_token"]

    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"client": "mobile", "refresh_token": mobile_tokens["refresh_token"]},
    )
    assert refresh_response.status_code == 200
    rotated = refresh_response.json()
    assert rotated["refresh_token"] != mobile_tokens["refresh_token"]

    replay_response = client.post(
        "/api/v1/auth/refresh",
        json={"client": "mobile", "refresh_token": mobile_tokens["refresh_token"]},
    )
    assert replay_response.status_code == 401


def test_invalid_login_and_inactive_user_are_rejected(
    client: TestClient,
    db: Session,
):
    email = _email()
    origin = {"Origin": "http://localhost:3000"}
    client.post(
        "/api/v1/auth/register",
        headers=origin,
        json={"email": email, "password": "correct horse battery staple", "client": "mobile"},
    )

    invalid = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "wrong password", "client": "mobile"},
    )
    assert invalid.status_code == 401

    user = db.exec(select(User).where(User.email == email)).one()
    user.is_active = False
    db.add(user)
    db.commit()

    inactive = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct horse battery staple", "client": "mobile"},
    )
    assert inactive.status_code == 403


def test_local_bearer_cannot_access_another_users_meeting(
    client: TestClient,
    db: Session,
):
    owner_email = _email()
    visitor_email = _email()
    owner_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": owner_email,
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )
    visitor_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": visitor_email,
            "password": "correct horse battery staple",
            "client": "mobile",
        },
    )
    assert owner_response.status_code == 201
    assert visitor_response.status_code == 201

    owner = db.exec(select(User).where(User.email == owner_email)).one()
    visitor_token = visitor_response.json()["access_token"]
    meeting = Meeting(
        owner_id=owner.id,
        title="Owner-only meeting",
        file_path=f"users/{owner.id}/meetings/owner-only.mp3",
        status="completed",
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    try:
        response = client.get(
            f"/api/v1/meetings/{meeting.id}",
            headers={"Authorization": f"Bearer {visitor_token}"},
        )
        assert response.status_code in (400, 403, 404)
    finally:
        owner_sessions = db.exec(
            select(AuthSession).where(AuthSession.user_id == owner.id)
        ).all()
        visitor = db.exec(select(User).where(User.email == visitor_email)).one()
        visitor_sessions = db.exec(
            select(AuthSession).where(AuthSession.user_id == visitor.id)
        ).all()
        db.delete(meeting)
        for session in [*owner_sessions, *visitor_sessions]:
            db.delete(session)
        db.delete(owner)
        db.delete(visitor)
        db.commit()
