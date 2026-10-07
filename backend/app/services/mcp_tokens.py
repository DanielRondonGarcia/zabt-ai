# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Repository operations for user-managed MCP bearer tokens."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.db.engine import engine
from app.models import MCPToken, User


MCP_TOKEN_PREFIX = "zabt_mcp_"
MCP_TOKEN_MIN_EXPIRY_DAYS = 1
MCP_TOKEN_MAX_EXPIRY_DAYS = 365


def _utc_now() -> datetime:
    """Return a naive UTC timestamp matching the existing database convention."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MCPTokenValidationError(ValueError):
    """Raised when a token label or lifetime is not acceptable."""


class MCPTokenService:
    """Persist and resolve MCP tokens without ever storing the raw secret."""

    def __init__(self, db_engine=engine):
        self.db_engine = db_engine

    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Return the lowercase SHA-256 digest used for lookup."""

        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def validate_label(label: str) -> str:
        if not isinstance(label, str):
            raise MCPTokenValidationError("label must be a string")
        normalized = label.strip()
        if not normalized:
            raise MCPTokenValidationError("label cannot be empty")
        if len(normalized) > 100:
            raise MCPTokenValidationError("label must not exceed 100 characters")
        return normalized

    @staticmethod
    def validate_expiry(expires_in_days: int) -> int:
        if isinstance(expires_in_days, bool) or not isinstance(expires_in_days, int):
            raise MCPTokenValidationError("expires_in_days must be an integer")
        if not MCP_TOKEN_MIN_EXPIRY_DAYS <= expires_in_days <= MCP_TOKEN_MAX_EXPIRY_DAYS:
            raise MCPTokenValidationError(
                "expires_in_days must be between "
                f"{MCP_TOKEN_MIN_EXPIRY_DAYS} and {MCP_TOKEN_MAX_EXPIRY_DAYS}"
            )
        return expires_in_days

    def create(
        self,
        user_id: int,
        label: str,
        expires_in_days: int,
    ) -> tuple[MCPToken, str]:
        """Create a token and return its model plus raw value exactly once."""

        normalized_label = self.validate_label(label)
        lifetime_days = self.validate_expiry(expires_in_days)
        raw_token = f"{MCP_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
        now = _utc_now()
        token = MCPToken(
            user_id=user_id,
            label=normalized_label,
            token_hash=self.hash_token(raw_token),
            token_prefix=raw_token[:16],
            created_at=now,
            expires_at=now + timedelta(days=lifetime_days),
        )

        with Session(self.db_engine) as session:
            if session.get(User, user_id) is None:
                raise MCPTokenValidationError("token owner was not found")
            session.add(token)
            session.commit()
            session.refresh(token)

        return token, raw_token

    def list(self, user_id: int) -> list[MCPToken]:
        """Return only tokens belonging to the requested owner."""

        with Session(self.db_engine) as session:
            statement = (
                select(MCPToken)
                .where(MCPToken.user_id == user_id)
                .order_by(MCPToken.created_at.desc(), MCPToken.id.desc())
            )
            return list(session.exec(statement).all())

    def revoke(self, token_id: int, user_id: int) -> bool:
        """Revoke an owner token; return false for missing or foreign tokens."""

        with Session(self.db_engine) as session:
            statement = select(MCPToken).where(
                MCPToken.id == token_id,
                MCPToken.user_id == user_id,
            )
            token = session.exec(statement).first()
            if token is None:
                return False
            if token.revoked_at is None:
                token.revoked_at = _utc_now()
                session.add(token)
                session.commit()
            return True

    def resolve(self, raw_token: str, owner_id: int | None = None) -> MCPToken | None:
        """Resolve a valid bearer token and update its last-use timestamp.

        ``owner_id`` is an optional defense-in-depth filter for owner-scoped
        callers. The MCP verifier intentionally leaves it unset and derives the
        owner only from the token row after the hash match.
        """

        if not isinstance(raw_token, str) or not raw_token or len(raw_token) > 256:
            return None

        token_hash = self.hash_token(raw_token.strip())
        with Session(self.db_engine) as session:
            token = session.exec(
                select(MCPToken).where(MCPToken.token_hash == token_hash)
            ).first()
            if token is None:
                return None
            if owner_id is not None and token.user_id != owner_id:
                return None
            if token.revoked_at is not None or _as_utc(token.expires_at) <= _as_utc(_utc_now()):
                return None
            owner = session.get(User, token.user_id)
            if owner is None or not owner.is_active:
                return None

            # Constant-time comparison is redundant after the unique indexed
            # lookup, but keeps the final equality explicit and defensive.
            if not hmac.compare_digest(token.token_hash, token_hash):
                return None

            token.last_used_at = _utc_now()
            session.add(token)
            session.commit()
            session.refresh(token)
            return token

    # Descriptive aliases keep the repository easy to discover at call sites.
    create_token = create
    list_for_user = list
    revoke_for_user = revoke
    resolve_bearer_token = resolve


mcp_token_service = MCPTokenService()
