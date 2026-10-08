# SPDX-License-Identifier: AGPL-3.0-only
"""Deployment bootstrap tests for the dedicated system superuser."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import JSON
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.models import User
from app.services import auth as auth_service
from app.services.system_superuser import (
    SystemSuperuserBootstrapError,
    ensure_system_superuser,
)


@pytest.fixture
def user_engine(tmp_path: Path):
    database = create_engine(
        f"sqlite:///{tmp_path / 'superuser.db'}",
        connect_args={"check_same_thread": False},
    )
    language_preferences = User.__table__.c.language_preferences
    original_type = language_preferences.type
    language_preferences.type = JSON()
    try:
        SQLModel.metadata.create_all(database, tables=[User.__table__])
        yield database
    finally:
        language_preferences.type = original_type
        database.dispose()


def set_bootstrap_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_EMAIL", "operator@example.com")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_PASSWORD", "correct horse battery staple")


def test_first_bootstrap_creates_one_hashed_system_superuser(
    user_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    set_bootstrap_credentials(monkeypatch)

    with Session(user_engine) as db:
        user = ensure_system_superuser(db)
        users = db.exec(select(User)).all()

    assert len(users) == 1
    assert user.email == "operator@example.com"
    assert user.is_superuser is True
    assert user.password_hash != "correct horse battery staple"
    assert auth_service.verify_password(
        "correct horse battery staple",
        user.password_hash,
    )


def test_bootstrap_promotes_only_the_configured_email_and_sets_password_once(
    user_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    initial_hash = auth_service.hash_password("existing account password")
    with Session(user_engine) as db:
        existing = User(
            email="operator@example.com",
            password_hash=initial_hash,
            is_admin=True,
            is_active=False,
        )
        db.add(existing)
        db.commit()

    set_bootstrap_credentials(monkeypatch)
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_PASSWORD", "existing account password")
    with Session(user_engine) as db:
        promoted = ensure_system_superuser(db)
        initial_hash = promoted.password_hash

    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_PASSWORD", "a later deployment password")
    with Session(user_engine) as db:
        existing_superuser = ensure_system_superuser(db)
        users = db.exec(select(User)).all()

    assert len(users) == 1
    assert promoted.is_superuser is True
    assert promoted.is_active is True
    assert promoted.is_admin is True
    assert promoted.password_hash == initial_hash
    assert existing_superuser.password_hash == initial_hash
    assert auth_service.verify_password(
        "existing account password",
        existing_superuser.password_hash,
    )
    assert not auth_service.verify_password(
        "correct horse battery staple",
        existing_superuser.password_hash,
    )


def test_bootstrap_refuses_existing_account_when_password_does_not_match(
    user_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    original_hash = auth_service.hash_password("existing account password")
    with Session(user_engine) as db:
        db.add(
            User(
                email="operator@example.com",
                password_hash=original_hash,
                is_active=True,
            )
        )
        db.commit()

    configured_password = "wrong bootstrap password"
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_EMAIL", "operator@example.com")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_PASSWORD", configured_password)
    with Session(user_engine) as db:
        with pytest.raises(SystemSuperuserBootstrapError) as error:
            ensure_system_superuser(db)
        existing = db.exec(select(User)).one()

    assert "could not be verified" in str(error.value)
    assert configured_password not in str(error.value)
    assert "operator@example.com" not in str(error.value)
    assert existing.is_superuser is False
    assert existing.password_hash == original_hash


def test_bootstrap_refuses_existing_account_without_password_hash(
    user_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    with Session(user_engine) as db:
        db.add(User(email="operator@example.com", password_hash=None, is_active=True))
        db.commit()

    set_bootstrap_credentials(monkeypatch)
    with Session(user_engine) as db:
        with pytest.raises(SystemSuperuserBootstrapError) as error:
            ensure_system_superuser(db)
        existing = db.exec(select(User)).one()

    assert "could not be verified" in str(error.value)
    assert "correct horse battery staple" not in str(error.value)
    assert existing.is_superuser is False
    assert existing.password_hash is None


def test_existing_superuser_bootstrap_is_idempotent_without_password_verification(
    user_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    original_hash = auth_service.hash_password("existing operator password")
    with Session(user_engine) as db:
        user = User(
            email="operator@example.com",
            password_hash=original_hash,
            is_active=True,
            is_superuser=True,
        )
        db.add(user)
        db.commit()
        user_id = user.id

    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_EMAIL", "operator@example.com")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_PASSWORD", "short")
    with Session(user_engine) as db:
        provisioned = ensure_system_superuser(db)

    assert provisioned.id == user_id
    assert provisioned.is_superuser is True
    assert provisioned.password_hash == original_hash


def test_missing_credentials_fail_only_before_a_superuser_exists(
    user_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_EMAIL", "")
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_PASSWORD", "")

    with Session(user_engine) as db:
        with pytest.raises(SystemSuperuserBootstrapError, match="required"):
            ensure_system_superuser(db)
        db.add(User(email="operator@example.com", password_hash="existing-hash", is_superuser=True))
        db.commit()
        provisioned = ensure_system_superuser(db)

    assert provisioned.is_superuser is True
    assert provisioned.password_hash == "existing-hash"


def test_bootstrap_email_mismatch_fails_without_exposing_credentials(
    user_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    with Session(user_engine) as db:
        db.add(User(email="operator@example.com", password_hash="existing-hash", is_superuser=True))
        db.commit()

    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_EMAIL", "replacement@example.com")
    secret_marker = "do-not-print-this-bootstrap-password"
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_PASSWORD", secret_marker)

    with Session(user_engine) as db:
        with pytest.raises(SystemSuperuserBootstrapError) as error:
            ensure_system_superuser(db)

    assert "does not match" in str(error.value)
    assert secret_marker not in str(error.value)
    assert "replacement@example.com" not in str(error.value)


def test_bootstrap_rejects_ambiguous_case_insensitive_email_matches(
    user_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    set_bootstrap_credentials(monkeypatch)
    with Session(user_engine) as db:
        db.add_all(
            [
                User(email="operator@example.com", password_hash="one"),
                User(email="Operator@Example.com", password_hash="two"),
            ]
        )
        db.commit()

    with Session(user_engine) as db:
        with pytest.raises(SystemSuperuserBootstrapError, match="Multiple accounts"):
            ensure_system_superuser(db)


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("not-an-email", "correct horse battery staple"),
        ("operator@example.com", "short"),
    ],
)
def test_invalid_bootstrap_credentials_fail_without_echoing_password(
    user_engine,
    monkeypatch: pytest.MonkeyPatch,
    email: str,
    password: str,
):
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_EMAIL", email)
    monkeypatch.setattr(settings, "BOOTSTRAP_SUPERUSER_PASSWORD", password)

    with Session(user_engine) as db:
        with pytest.raises(SystemSuperuserBootstrapError) as error:
            ensure_system_superuser(db)

    assert password not in str(error.value)


def test_sqlite_fallback_serializes_concurrent_bootstrap_creation(
    user_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    set_bootstrap_credentials(monkeypatch)
    monkeypatch.setattr(auth_service, "hash_password", lambda _password: "hashed")

    def bootstrap() -> int:
        with Session(user_engine) as db:
            return ensure_system_superuser(db).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        user_ids = list(pool.map(lambda _index: bootstrap(), range(2)))

    with Session(user_engine) as db:
        users = db.exec(select(User)).all()

    assert len(users) == 1
    assert user_ids == [users[0].id, users[0].id]


class _EmptyResult:
    def all(self):
        return []

    def first(self):
        return None


class _PostgresBootstrapSession:
    def __init__(self):
        self.advisory_locks = []
        self.added = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def exec(self, statement, params=None):
        if "pg_advisory_xact_lock" in str(statement):
            self.advisory_locks.append(params)
        return _EmptyResult()

    def add(self, model):
        model.id = 1
        self.added.append(model)

    def commit(self):
        pass

    def refresh(self, _model):
        pass

    def rollback(self):
        pass


def test_bootstrap_uses_postgresql_transaction_advisory_lock(
    monkeypatch: pytest.MonkeyPatch,
):
    set_bootstrap_credentials(monkeypatch)
    monkeypatch.setattr(auth_service, "hash_password", lambda _password: "hashed")
    db = _PostgresBootstrapSession()

    user = ensure_system_superuser(db)

    assert user.is_superuser is True
    assert db.advisory_locks == [
        {"lock_key": auth_service._AUTHENTICATION_POLICY_ADVISORY_LOCK_KEY}
    ]
