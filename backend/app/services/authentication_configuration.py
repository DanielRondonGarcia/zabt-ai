# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Persistence and readiness checks for the global authentication mode."""

from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import (
    AuthenticationConfiguration,
    ExternalIdentity,
    ExternalIdentityProvider,
    User,
    UserLoginMode,
)


def get_authentication_configuration(
    db: Session,
) -> AuthenticationConfiguration | None:
    return db.exec(
        select(AuthenticationConfiguration).where(AuthenticationConfiguration.id == 1)
    ).first()


def get_user_login_mode(db: Session) -> UserLoginMode:
    configuration = get_authentication_configuration(db)
    if configuration is None:
        return UserLoginMode.LOCAL
    return UserLoginMode(configuration.user_login_mode)


def set_user_login_mode(
    db: Session,
    *,
    mode: UserLoginMode,
    updated_by: int,
) -> AuthenticationConfiguration:
    configuration = get_authentication_configuration(db)
    if configuration is None:
        configuration = AuthenticationConfiguration(
            id=1,
            user_login_mode=mode,
            updated_at=datetime.utcnow(),
            updated_by=updated_by,
        )
    else:
        configuration.user_login_mode = mode
        configuration.updated_at = datetime.utcnow()
        configuration.updated_by = updated_by
    db.add(configuration)
    db.flush()
    return configuration


def _unlinked_active_local_accounts_query():
    linked_microsoft_identity = (
        select(ExternalIdentity.id)
        .where(
            ExternalIdentity.user_id == User.id,
            ExternalIdentity.provider == ExternalIdentityProvider.MICROSOFT,
        )
        .exists()
    )
    return select(func.count(User.id)).where(
        User.is_active.is_(True),
        User.is_superuser.is_(False),
        ~linked_microsoft_identity,
    )


def count_unlinked_active_local_accounts(db: Session) -> int:
    count = db.exec(_unlinked_active_local_accounts_query()).one()
    return int(count)


def count_unlinked_passwordless_accounts(db: Session) -> int:
    count = db.exec(
        _unlinked_active_local_accounts_query().where(User.password_hash.is_(None))
    ).one()
    return int(count)
