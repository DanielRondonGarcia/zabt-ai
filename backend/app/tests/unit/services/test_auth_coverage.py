# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused unit coverage for local authentication service behavior.

These tests avoid app startup and external services. Database-facing branches use a
small faithful session fake so service behavior is exercised without relying on a
PostgreSQL-specific model metadata bind.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import Request, Response
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AuthSession, User
from app.services import auth


class ExecResult:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeSession:
    def __init__(self, exec_rows=None, users=None, commit_error: Exception | None = None):
        self.exec_rows = list(exec_rows or [])
        self.users = dict(users or {})
        self.commit_error = commit_error
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.refreshed = []

    def exec(self, _statement):
        rows = self.exec_rows.pop(0) if self.exec_rows else []
        return ExecResult(rows)

    def get(self, model, obj_id):
        if model is User:
            return self.users.get(obj_id)
        raise AssertionError(f"Unexpected model lookup: {model}")

    def add(self, obj):
        if isinstance(obj, User) and obj.id is None:
            obj.id = len([item for item in self.added if isinstance(item, User)]) + 1
        if isinstance(obj, AuthSession) and obj.id is None:
            obj.id = len([item for item in self.added if isinstance(item, AuthSession)]) + 1
        self.added.append(obj)

    def commit(self):
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, obj):
        self.refreshed.append(obj)


def make_user(**overrides) -> User:
    data = {
        "id": 1,
        "email": "user@example.com",
        "password_hash": "hash",
        "is_active": True,
    }
    data.update(overrides)
    return User(**data)


def make_session(**overrides) -> AuthSession:
    now = datetime(2026, 1, 1, 12, 0, 0)
    data = {
        "id": 1,
        "user_id": 1,
        "refresh_token_hash": auth._hash_refresh_token("refresh-token"),
        "created_at": now,
        "expires_at": now + timedelta(days=1),
        "revoked_at": None,
    }
    data.update(overrides)
    return AuthSession(**data)


def make_request(*, cookie: str | None = None, origin: str = "https://app.example.com") -> Request:
    headers = [(b"origin", origin.encode())]
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    return Request({"type": "http", "method": "POST", "path": "/auth/refresh", "headers": headers})


@pytest.fixture(autouse=True)
def deterministic_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth.settings, "AUTH_REFRESH_SESSION_EXPIRE_DAYS", 7)
    monkeypatch.setattr(auth.settings, "AUTH_ACCESS_COOKIE_NAME", "access_cookie")
    monkeypatch.setattr(auth.settings, "AUTH_REFRESH_COOKIE_NAME", "refresh_cookie")
    monkeypatch.setattr(auth.settings, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(auth.settings, "AUTH_COOKIE_SAMESITE", "lax")
    monkeypatch.setattr(auth.settings, "AUTH_COOKIE_DOMAIN", None)


def test_normalize_email_accepts_casefold_trim_and_localhost():
    assert auth.normalize_email("  USER@Example.COM  ") == "user@example.com"
    assert auth.normalize_email("Dev@LOCALHOST") == "dev@localhost"


@pytest.mark.parametrize(
    "email",
    [
        "missing-at.example.com",
        "two@@example.com",
        "@example.com",
        "user@",
        "user name@example.com",
        "user@example",
        f"a@{'b' * 319}.com",
    ],
)
def test_normalize_email_rejects_invalid_shapes(email: str):
    with pytest.raises(ValueError, match="valid email"):
        auth.normalize_email(email)


def test_hash_and_verify_password_round_trip_and_rejects_bad_hashes():
    password_hash = auth.hash_password("correct horse battery staple")

    assert password_hash != "correct horse battery staple"
    assert auth.verify_password("correct horse battery staple", password_hash) is True
    assert auth.verify_password("wrong", password_hash) is False
    assert auth.verify_password("password", None) is False
    assert auth.verify_password("password", "not-a-passlib-hash") is False


def test_create_user_normalizes_trims_name_hashes_and_commits(monkeypatch: pytest.MonkeyPatch):
    fake_db = FakeSession(exec_rows=[[]])
    monkeypatch.setattr(auth, "hash_password", lambda password: f"hashed:{password}")

    user = auth.create_user(fake_db, email=" USER@Example.COM ", password="secret", full_name="  Ada  ")

    assert user.email == "user@example.com"
    assert user.full_name == "Ada"
    assert user.password_hash == "hashed:secret"
    assert user.supabase_id is None
    assert user.is_active is True
    assert fake_db.added == [user]
    assert fake_db.commits == 1
    assert fake_db.refreshed == [user]


def test_create_user_rejects_existing_normalized_email():
    fake_db = FakeSession(exec_rows=[[make_user()]])

    with pytest.raises(auth.DuplicateEmailError):
        auth.create_user(fake_db, email="user@example.com", password="secret")

    assert fake_db.added == []
    assert fake_db.commits == 0


def test_create_user_converts_unique_constraint_race_to_duplicate(monkeypatch: pytest.MonkeyPatch):
    fake_db = FakeSession(
        exec_rows=[[]],
        commit_error=IntegrityError("insert", {}, Exception("unique")),
    )
    monkeypatch.setattr(auth, "hash_password", lambda _password: "hashed")

    with pytest.raises(auth.DuplicateEmailError):
        auth.create_user(fake_db, email="user@example.com", password="secret")

    assert fake_db.rollbacks == 1


def test_authenticate_user_rejects_unknown_bad_password_and_inactive(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "verify_password", lambda password, password_hash: password == "ok" and password_hash == "hash")

    with pytest.raises(auth.InvalidCredentialsError):
        auth.authenticate_user(FakeSession(exec_rows=[[]]), email="missing@example.com", password="ok")

    with pytest.raises(auth.InvalidCredentialsError):
        auth.authenticate_user(FakeSession(exec_rows=[[make_user()]]), email="user@example.com", password="bad")

    with pytest.raises(auth.InactiveUserError):
        auth.authenticate_user(
            FakeSession(exec_rows=[[make_user(is_active=False)]]),
            email="user@example.com",
            password="ok",
        )


def test_authenticate_user_returns_active_user(monkeypatch: pytest.MonkeyPatch):
    user = make_user()
    monkeypatch.setattr(auth, "verify_password", lambda _password, _hash: True)

    assert auth.authenticate_user(FakeSession(exec_rows=[[user]]), email="USER@example.com", password="ok") is user


def test_issue_tokens_creates_refresh_session_and_access_token(monkeypatch: pytest.MonkeyPatch):
    now = datetime(2026, 1, 1, 12, 0, 0)
    fake_db = FakeSession()
    monkeypatch.setattr(auth, "_utc_now", lambda: now)
    monkeypatch.setattr(auth, "_new_refresh_token", lambda: "refresh-token")
    monkeypatch.setattr(auth.security, "create_access_token", lambda user_id: (f"access-for-{user_id}", 900))

    bundle = auth.issue_tokens(fake_db, make_user(id=42))

    assert bundle.access_token == "access-for-42"
    assert bundle.refresh_token == "refresh-token"
    assert bundle.expires_in == 900
    stored_session = fake_db.added[0]
    assert isinstance(stored_session, AuthSession)
    assert stored_session.user_id == 42
    assert stored_session.refresh_token_hash == auth._hash_refresh_token("refresh-token")
    assert stored_session.expires_at == now + timedelta(days=7)
    assert fake_db.commits == 1


def test_rotate_refresh_token_rejects_missing_unknown_revoked_and_expired(monkeypatch: pytest.MonkeyPatch):
    now = datetime(2026, 1, 1, 12, 0, 0)
    monkeypatch.setattr(auth, "_utc_now", lambda: now)

    with pytest.raises(auth.InvalidRefreshTokenError):
        auth.rotate_refresh_token(FakeSession(), "")

    with pytest.raises(auth.InvalidRefreshTokenError):
        auth.rotate_refresh_token(FakeSession(exec_rows=[[]]), "refresh-token")

    with pytest.raises(auth.InvalidRefreshTokenError):
        auth.rotate_refresh_token(
            FakeSession(exec_rows=[[make_session(revoked_at=now)]]),
            "refresh-token",
        )

    with pytest.raises(auth.InvalidRefreshTokenError):
        auth.rotate_refresh_token(
            FakeSession(exec_rows=[[make_session(expires_at=now)]]),
            "refresh-token",
        )


def test_rotate_refresh_token_revokes_when_user_missing_or_inactive(monkeypatch: pytest.MonkeyPatch):
    now = datetime(2026, 1, 1, 12, 0, 0)
    monkeypatch.setattr(auth, "_utc_now", lambda: now)

    missing_user_session = make_session()
    missing_user_db = FakeSession(exec_rows=[[missing_user_session]], users={})
    with pytest.raises(auth.InvalidRefreshTokenError):
        auth.rotate_refresh_token(missing_user_db, "refresh-token")
    assert missing_user_session.revoked_at == now
    assert missing_user_db.commits == 1

    inactive_session = make_session()
    inactive_db = FakeSession(exec_rows=[[inactive_session]], users={1: make_user(is_active=False)})
    with pytest.raises(auth.InactiveUserError):
        auth.rotate_refresh_token(inactive_db, "refresh-token")
    assert inactive_session.revoked_at == now
    assert inactive_db.commits == 1


def test_rotate_refresh_token_revokes_old_session_and_returns_replacement(monkeypatch: pytest.MonkeyPatch):
    now = datetime(2026, 1, 1, 12, 0, 0)
    old_session = make_session()
    user = make_user(id=1)
    fake_db = FakeSession(exec_rows=[[old_session]], users={1: user})
    monkeypatch.setattr(auth, "_utc_now", lambda: now)
    monkeypatch.setattr(auth, "_new_refresh_token", lambda: "replacement-refresh")
    monkeypatch.setattr(auth.security, "create_access_token", lambda user_id: (f"replacement-access-{user_id}", 1200))

    rotated_user, bundle = auth.rotate_refresh_token(fake_db, "refresh-token")

    assert rotated_user is user
    assert old_session.revoked_at == now
    assert old_session.last_used_at == now
    assert bundle.access_token == "replacement-access-1"
    assert bundle.refresh_token == "replacement-refresh"
    assert bundle.expires_in == 1200
    assert fake_db.added[0].refresh_token_hash == auth._hash_refresh_token("replacement-refresh")
    assert fake_db.commits == 1


def test_revoke_refresh_token_is_noop_for_missing_or_unknown_token():
    empty_db = FakeSession()
    auth.revoke_refresh_token(empty_db, None)
    assert empty_db.commits == 0

    unknown_db = FakeSession(exec_rows=[[]])
    auth.revoke_refresh_token(unknown_db, "unknown")
    assert unknown_db.commits == 0


def test_revoke_refresh_token_marks_active_session_only(monkeypatch: pytest.MonkeyPatch):
    now = datetime(2026, 1, 1, 12, 0, 0)
    monkeypatch.setattr(auth, "_utc_now", lambda: now)

    active_session = make_session()
    active_db = FakeSession(exec_rows=[[active_session]])
    auth.revoke_refresh_token(active_db, "refresh-token")
    assert active_session.revoked_at == now
    assert active_db.added == [active_session]
    assert active_db.commits == 1

    revoked_session = make_session(revoked_at=now)
    revoked_db = FakeSession(exec_rows=[[revoked_session]])
    auth.revoke_refresh_token(revoked_db, "refresh-token")
    assert revoked_db.commits == 0


def test_set_and_clear_web_auth_cookies_use_expected_names_and_options():
    response = Response()
    auth.set_web_auth_cookies(response, auth.TokenBundle("access-value", "refresh-value", 60))

    cookie_headers = [value.decode() for name, value in response.raw_headers if name == b"set-cookie"]
    assert len(cookie_headers) == 2
    assert any(header.startswith("access_cookie=access-value") and "HttpOnly" in header and "Secure" in header and "Max-Age=60" in header for header in cookie_headers)
    assert any(header.startswith("refresh_cookie=refresh-value") and "HttpOnly" in header and "Secure" in header and "Max-Age=604800" in header for header in cookie_headers)

    clear_response = Response()
    auth.clear_web_auth_cookies(clear_response)
    clear_headers = [value.decode() for name, value in clear_response.raw_headers if name == b"set-cookie"]
    assert len(clear_headers) == 2
    assert all("Max-Age=0" in header for header in clear_headers)
    assert {header.split("=", 1)[0] for header in clear_headers} == {"access_cookie", "refresh_cookie"}


def test_refresh_token_from_request_uses_web_cookie_after_origin_validation(monkeypatch: pytest.MonkeyPatch):
    origins = []

    def record_origin(request: Request):
        origins.append(request.headers.get("origin"))

    monkeypatch.setattr(auth.security, "validate_request_origin", record_origin)

    token = auth.refresh_token_from_request(
        make_request(cookie="refresh_cookie=web-refresh; other=value"),
        client="web",
        body_token="mobile-refresh",
    )

    assert token == "web-refresh"
    assert origins == ["https://app.example.com"]


def test_refresh_token_from_request_propagates_invalid_web_origin(monkeypatch: pytest.MonkeyPatch):
    def reject_origin(_request: Request):
        raise ValueError("bad origin")

    monkeypatch.setattr(auth.security, "validate_request_origin", reject_origin)

    with pytest.raises(ValueError, match="bad origin"):
        auth.refresh_token_from_request(make_request(cookie="refresh_cookie=web-refresh"), client="web", body_token=None)


def test_refresh_token_from_request_uses_body_for_mobile_without_origin_validation(monkeypatch: pytest.MonkeyPatch):
    def fail_if_called(_request: Request):
        raise AssertionError("mobile clients must not require web origin validation")

    monkeypatch.setattr(auth.security, "validate_request_origin", fail_if_called)

    assert auth.refresh_token_from_request(make_request(), client="mobile", body_token="mobile-refresh") == "mobile-refresh"
