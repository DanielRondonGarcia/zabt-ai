# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""One-time provisioning for the deployment-owned system superuser."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.models import User
from app.services import auth as auth_service


class SystemSuperuserBootstrapError(RuntimeError):
    """Raised when startup cannot safely provision the configured operator."""


def _configured_credentials() -> tuple[str | None, str | None]:
    return (
        settings.BOOTSTRAP_SUPERUSER_EMAIL,
        settings.BOOTSTRAP_SUPERUSER_PASSWORD,
    )


def _validated_email(value: str) -> str:
    try:
        return auth_service.normalize_email(value)
    except (TypeError, ValueError) as exc:
        raise SystemSuperuserBootstrapError(
            "BOOTSTRAP_SUPERUSER_EMAIL must be a valid email address."
        ) from None


def _validate_password(value: str) -> None:
    if not 8 <= len(value) <= 128 or not value.strip():
        raise SystemSuperuserBootstrapError(
            "BOOTSTRAP_SUPERUSER_PASSWORD must contain 8 to 128 non-blank characters."
        )


def ensure_system_superuser(db: Session) -> User:
    """Create or password-proven promote the configured operator without resets."""

    configured_email, configured_password = _configured_credentials()
    normalized_email: str | None = None
    if configured_email is not None and configured_email.strip():
        normalized_email = _validated_email(configured_email)
    if configured_password is not None and not configured_password.strip():
        configured_password = None
    with auth_service._authentication_policy_lock(db):
        superusers = db.exec(
            select(User).where(User.is_superuser.is_(True)).order_by(User.id)
        ).all()
        if len(superusers) > 1:
            raise SystemSuperuserBootstrapError(
                "Multiple system superusers exist; resolve the database state before startup."
            )

        if superusers:
            existing_superuser = superusers[0]
            if normalized_email is not None:
                existing_email = _validated_email(existing_superuser.email)
                if existing_email != normalized_email:
                    raise SystemSuperuserBootstrapError(
                        "Configured bootstrap email does not match the existing system superuser."
                    )
            return existing_superuser

        if normalized_email is None or configured_password is None:
            raise SystemSuperuserBootstrapError(
                "BOOTSTRAP_SUPERUSER_EMAIL and BOOTSTRAP_SUPERUSER_PASSWORD are required "
                "until a system superuser has been provisioned."
            )
        _validate_password(configured_password)

        matching_accounts = db.exec(
            select(User).where(func.lower(User.email) == normalized_email)
        ).all()
        if len(matching_accounts) > 1:
            raise SystemSuperuserBootstrapError(
                "Multiple accounts match the configured bootstrap email; resolve the database state first."
            )
        user = matching_accounts[0] if matching_accounts else None
        if user is None:
            user = User(
                email=normalized_email,
                password_hash=auth_service.hash_password(configured_password),
                is_active=True,
                is_superuser=True,
            )
        else:
            if not auth_service.verify_password(configured_password, user.password_hash):
                raise SystemSuperuserBootstrapError(
                    "System superuser bootstrap credentials could not be verified."
                )
            user.email = normalized_email
            user.is_active = True
            user.is_superuser = True

        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise SystemSuperuserBootstrapError(
                "System superuser provisioning failed; verify the database state and retry."
            ) from None
        db.refresh(user)
        return user
