# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Application service for first-party local authentication."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import secrets
import threading
from typing import Iterator

from fastapi import Request, Response
from passlib.context import CryptContext
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core import security
from app.core.config import settings
from app.models import AuthSession, User


password_context = CryptContext(
    # bcrypt_sha256 preserves bcrypt's costed password hash while avoiding the
    # historic 72-byte truncation behavior for long Unicode passphrases.
    schemes=["bcrypt_sha256"],
    deprecated="auto",
    bcrypt_sha256__rounds=12,
)

_FIRST_ADMIN_ADVISORY_LOCK_KEY = 7_846_213_901
_FIRST_ADMIN_FALLBACK_LOCK = threading.Lock()


@contextmanager
def _first_admin_bootstrap_lock(db: Session) -> Iterator[None]:
    """Serialize the first-user check across PostgreSQL workers and test SQLite."""

    bind = None
    get_bind = getattr(db, "get_bind", None)
    if callable(get_bind):
        try:
            bind = get_bind()
        except Exception:
            bind = None
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect_name == "postgresql":
        # Transaction-scoped advisory locks are released by commit/rollback;
        # all application workers therefore serialize the bootstrap check.
        db.exec(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _FIRST_ADMIN_ADVISORY_LOCK_KEY},
        )
        yield
        return

    # SQLite test databases do not expose PostgreSQL advisory locks. The
    # process lock keeps the fallback deterministic while the production path
    # remains database-coordinated.
    with _FIRST_ADMIN_FALLBACK_LOCK:
        yield


class DuplicateEmailError(ValueError):
    """Raised when a normalized email already belongs to a local account."""


class InvalidCredentialsError(ValueError):
    """Raised when email/password credentials do not match."""


class InvalidRefreshTokenError(ValueError):
    """Raised for missing, expired, revoked, or unknown refresh sessions."""


class InactiveUserError(ValueError):
    """Raised when a disabled account attempts to authenticate."""


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_in: int


def normalize_email(email: str) -> str:
    """Normalize local account identifiers consistently at every boundary."""

    normalized = email.strip().casefold()
    if (
        len(normalized) > 320
        or any(character.isspace() for character in normalized)
        or normalized.count("@") != 1
        or normalized.startswith("@")
        or normalized.endswith("@")
    ):
        raise ValueError("Enter a valid email address")
    local, domain = normalized.split("@", 1)
    # Localhost is useful in tests and self-hosted development. Other domains
    # need a minimally shaped host part.
    if not local or (domain != "localhost" and "." not in domain):
        raise ValueError("Enter a valid email address")
    return normalized


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return password_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def _utc_now() -> datetime:
    return datetime.utcnow()


def _hash_refresh_token(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def _new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def _new_refresh_session(db: Session, user_id: int) -> str:
    raw_token = _new_refresh_token()
    now = _utc_now()
    session = AuthSession(
        user_id=user_id,
        refresh_token_hash=_hash_refresh_token(raw_token),
        created_at=now,
        expires_at=now + timedelta(days=settings.AUTH_REFRESH_SESSION_EXPIRE_DAYS),
    )
    db.add(session)
    return raw_token


def create_user(
    db: Session,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
) -> User:
    """Create a local user and never persist a plaintext password."""

    normalized_email = normalize_email(email)
    existing = db.exec(
        select(User).where(func.lower(User.email) == normalized_email)
    ).first()
    if existing is not None:
        raise DuplicateEmailError

    # A migrated installation bootstraps its earliest existing user through
    # Alembic. A brand-new installation has no row for the migration to mark,
    # so the first registration receives the same bootstrap capability. The
    # lock must cover the check and commit or concurrent first registrations
    # could both observe an empty table.
    with _first_admin_bootstrap_lock(db):
        first_user_id = db.exec(select(User.id).order_by(User.id).limit(1)).first()

        user = User(
            email=normalized_email,
            full_name=full_name.strip() if full_name and full_name.strip() else None,
            password_hash=hash_password(password),
            supabase_id=None,
            is_active=True,
            is_admin=first_user_id is None,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            # The lookup above prevents the normal duplicate path; this handles a
            # concurrent registration racing the unique database constraint.
            raise DuplicateEmailError from exc
    db.refresh(user)
    return user


def authenticate_user(db: Session, *, email: str, password: str) -> User:
    """Authenticate a local account without revealing which check failed."""

    normalized_email = normalize_email(email)
    user = db.exec(
        select(User).where(func.lower(User.email) == normalized_email)
    ).first()
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("Invalid email or password")
    if not user.is_active:
        raise InactiveUserError
    return user


def issue_tokens(db: Session, user: User) -> TokenBundle:
    access_token, expires_in = security.create_access_token(user.id)
    refresh_token = _new_refresh_session(db, user.id)
    db.commit()
    return TokenBundle(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


def rotate_refresh_token(db: Session, raw_refresh_token: str) -> tuple[User, TokenBundle]:
    """Atomically revoke one refresh session and issue its replacement."""

    if not raw_refresh_token:
        raise InvalidRefreshTokenError

    session = db.exec(
        select(AuthSession)
        .where(AuthSession.refresh_token_hash == _hash_refresh_token(raw_refresh_token))
        .with_for_update()
    ).first()
    now = _utc_now()
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at <= now
    ):
        raise InvalidRefreshTokenError

    user = db.get(User, session.user_id)
    if user is None:
        session.revoked_at = now
        db.commit()
        raise InvalidRefreshTokenError
    if not user.is_active:
        session.revoked_at = now
        db.commit()
        raise InactiveUserError

    session.revoked_at = now
    session.last_used_at = now
    replacement = _new_refresh_session(db, user.id)
    access_token, expires_in = security.create_access_token(user.id)
    db.commit()
    return user, TokenBundle(
        access_token=access_token,
        refresh_token=replacement,
        expires_in=expires_in,
    )


def revoke_refresh_token(db: Session, raw_refresh_token: str | None) -> None:
    """Revoke a refresh session if present; do not expose token state."""

    if not raw_refresh_token:
        return
    session = db.exec(
        select(AuthSession).where(
            AuthSession.refresh_token_hash == _hash_refresh_token(raw_refresh_token)
        )
    ).first()
    if session is not None and session.revoked_at is None:
        session.revoked_at = _utc_now()
        db.add(session)
        db.commit()


def set_web_auth_cookies(response: Response, tokens: TokenBundle) -> None:
    cookie_options = {
        "httponly": True,
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
        "domain": settings.AUTH_COOKIE_DOMAIN,
        "path": "/",
    }
    response.set_cookie(
        key=settings.AUTH_ACCESS_COOKIE_NAME,
        value=tokens.access_token,
        max_age=tokens.expires_in,
        **cookie_options,
    )
    response.set_cookie(
        key=settings.AUTH_REFRESH_COOKIE_NAME,
        value=tokens.refresh_token,
        max_age=settings.AUTH_REFRESH_SESSION_EXPIRE_DAYS * 24 * 60 * 60,
        **cookie_options,
    )


def clear_web_auth_cookies(response: Response) -> None:
    options = {
        "domain": settings.AUTH_COOKIE_DOMAIN,
        "path": "/",
    }
    response.delete_cookie(key=settings.AUTH_ACCESS_COOKIE_NAME, **options)
    response.delete_cookie(key=settings.AUTH_REFRESH_COOKIE_NAME, **options)


def refresh_token_from_request(request: Request, *, client: str, body_token: str | None) -> str | None:
    """Choose the credential according to the explicit web/mobile contract."""

    if client == "web":
        security.validate_request_origin(request)
        return request.cookies.get(settings.AUTH_REFRESH_COOKIE_NAME)
    return body_token
