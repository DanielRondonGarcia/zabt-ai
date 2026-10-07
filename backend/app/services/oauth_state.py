# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""One-time OAuth transaction state backed by Redis."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import re
import secrets
from typing import Any
from urllib.parse import urlsplit

import redis

from app.core.config import settings


OAUTH_STATE_TTL_SECONDS = 600
_STATE_KEY_PREFIX = "zabt:oauth-state:"
_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_CODE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_ALLOWED_PURPOSES = frozenset({"oidc_login", "oidc_link"})


class OAuthStateError(RuntimeError):
    """Raised when the OAuth state store is unavailable or cannot write."""


@dataclass(frozen=True)
class OAuthStateTransaction:
    """The state returned to the provider and its server-side transaction data."""

    state: str
    purpose: str
    nonce: str
    code_verifier: str
    next_path: str
    user_id: int | None = None


def validate_next_path(next_path: str | None) -> str:
    """Accept only a same-origin relative path for the post-login redirect."""

    if next_path is None or next_path == "":
        return "/"
    if not isinstance(next_path, str) or len(next_path) > 2048:
        raise ValueError("Invalid next path")
    if not next_path.startswith("/") or next_path.startswith("//"):
        raise ValueError("Invalid next path")
    if "\\" in next_path or "#" in next_path:
        raise ValueError("Invalid next path")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in next_path):
        raise ValueError("Invalid next path")

    parsed = urlsplit(next_path)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        raise ValueError("Invalid next path")
    return next_path


def build_pkce_challenge(code_verifier: str) -> str:
    """Build the RFC 7636 S256 challenge for a stored verifier."""

    if not isinstance(code_verifier, str) or not _CODE_VERIFIER_RE.fullmatch(code_verifier):
        raise ValueError("Invalid PKCE code verifier")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OAuthStateService:
    """Persist opaque OAuth state and consume it atomically exactly once."""

    def __init__(
        self,
        redis_client: Any | None = None,
        *,
        redis_url: str | None = None,
        ttl_seconds: int = OAUTH_STATE_TTL_SECONDS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("OAuth state TTL must be positive")
        self._redis = (
            redis_client
            if redis_client is not None
            else redis.Redis.from_url(
                redis_url or settings.REDIS_URL,
                decode_responses=True,
            )
        )
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(state: str) -> str:
        digest = hashlib.sha256(state.encode("ascii")).hexdigest()
        return f"{_STATE_KEY_PREFIX}{digest}"

    def create_transaction(
        self,
        *,
        purpose: str,
        next_path: str | None = None,
        user_id: int | None = None,
        nonce: str | None = None,
        code_verifier: str | None = None,
    ) -> OAuthStateTransaction:
        if purpose not in _ALLOWED_PURPOSES:
            raise ValueError("Unsupported OAuth state purpose")
        if purpose == "oidc_link" and user_id is None:
            raise ValueError("OIDC link state requires a user")
        if purpose == "oidc_login" and user_id is not None:
            raise ValueError("OIDC login state cannot include a user")
        if user_id is not None and (
            isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0
        ):
            raise ValueError("Invalid OAuth state user")

        safe_next = validate_next_path(next_path)
        transaction = OAuthStateTransaction(
            state=secrets.token_urlsafe(32),
            purpose=purpose,
            nonce=nonce or secrets.token_urlsafe(32),
            code_verifier=code_verifier or secrets.token_urlsafe(64),
            next_path=safe_next,
            user_id=user_id,
        )
        if not transaction.nonce or not transaction.code_verifier:
            raise ValueError("Invalid OAuth transaction material")
        if not _CODE_VERIFIER_RE.fullmatch(transaction.code_verifier):
            raise ValueError("Invalid PKCE code verifier")

        payload = {
            "purpose": transaction.purpose,
            "nonce": transaction.nonce,
            "code_verifier": transaction.code_verifier,
            "next_path": transaction.next_path,
            "user_id": transaction.user_id,
        }
        try:
            written = self._redis.set(
                self._key(transaction.state),
                json.dumps(payload, separators=(",", ":")),
                ex=self._ttl_seconds,
                nx=True,
            )
        except Exception as exc:
            raise OAuthStateError("OAuth state store unavailable") from exc
        if not written:
            raise OAuthStateError("Could not reserve OAuth state")
        return transaction

    def consume(self, state: str | None) -> OAuthStateTransaction | None:
        """Atomically GETDEL a state value; missing, expired, or malformed is invalid."""

        if not isinstance(state, str) or not _STATE_RE.fullmatch(state):
            return None
        try:
            raw = self._redis.getdel(self._key(state))
        except Exception as exc:
            raise OAuthStateError("OAuth state store unavailable") from exc
        if raw is None:
            return None

        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
            return self._parse_transaction(state, payload)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return None

    @staticmethod
    def _parse_transaction(state: str, payload: Any) -> OAuthStateTransaction:
        if not isinstance(payload, dict):
            raise ValueError("Malformed OAuth state payload")
        purpose = payload.get("purpose")
        nonce = payload.get("nonce")
        code_verifier = payload.get("code_verifier")
        next_path = payload.get("next_path")
        user_id = payload.get("user_id")
        if purpose not in _ALLOWED_PURPOSES:
            raise ValueError("Malformed OAuth state purpose")
        if not isinstance(nonce, str) or not 16 <= len(nonce) <= 256:
            raise ValueError("Malformed OAuth state nonce")
        if not isinstance(code_verifier, str) or not _CODE_VERIFIER_RE.fullmatch(code_verifier):
            raise ValueError("Malformed OAuth state verifier")
        if not isinstance(next_path, str):
            raise ValueError("Malformed OAuth state next path")
        safe_next = validate_next_path(next_path)
        if user_id is not None and (
            isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0
        ):
            raise ValueError("Malformed OAuth state user")
        if purpose == "oidc_link" and user_id is None:
            raise ValueError("Malformed OAuth link owner")
        if purpose == "oidc_login" and user_id is not None:
            raise ValueError("Malformed OAuth login owner")
        return OAuthStateTransaction(
            state=state,
            purpose=purpose,
            nonce=nonce,
            code_verifier=code_verifier,
            next_path=safe_next,
            user_id=user_id,
        )
