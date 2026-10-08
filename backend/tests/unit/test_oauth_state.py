# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused tests for Redis-backed one-time OAuth state."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from app.services.oauth_state import (
    OAuthStateService,
    build_pkce_challenge,
    validate_next_path,
)


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.set_calls: list[dict[str, object]] = []

    def set(self, key, value, *, ex, nx):
        self.set_calls.append({"key": key, "value": value, "ex": ex, "nx": nx})
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def getdel(self, key):
        return self.values.pop(key, None)


def test_state_is_opaque_hashed_and_consumed_once():
    redis_client = FakeRedis()
    service = OAuthStateService(redis_client, ttl_seconds=300)

    transaction = service.create_transaction(
        purpose="oidc_link",
        client="web",
        next_path="/meetings?filter=upcoming",
        user_id=42,
        nonce="n" * 32,
        code_verifier="v" * 43,
    )

    stored = redis_client.set_calls[0]
    assert transaction.state not in stored["key"]
    assert stored["ex"] == 300
    assert stored["nx"] is True
    payload = json.loads(stored["value"])
    assert payload == {
        "purpose": "oidc_link",
        "nonce": "n" * 32,
        "code_verifier": "v" * 43,
        "next_path": "/meetings?filter=upcoming",
        "user_id": 42,
        "client": "web",
    }

    consumed = service.consume(transaction.state)
    assert consumed == transaction
    assert service.consume(transaction.state) is None


def test_expired_and_malformed_state_fail_closed():
    redis_client = FakeRedis()
    service = OAuthStateService(redis_client)
    assert service.consume("too-short") is None

    transaction = service.create_transaction(
        purpose="oidc_login",
        client="mobile",
        nonce="n" * 32,
        code_verifier="v" * 43,
    )
    redis_client.values[service._key(transaction.state)] = "not-json"
    assert service.consume(transaction.state) is None
    assert service.consume(transaction.state) is None

    expired = service.create_transaction(
        purpose="oidc_login",
        client="web",
        nonce="n" * 32,
        code_verifier="v" * 43,
    )
    redis_client.values.pop(service._key(expired.state))
    assert service.consume(expired.state) is None


def test_link_state_requires_an_owner_user_id():
    with pytest.raises(ValueError, match="requires a user"):
        OAuthStateService(FakeRedis()).create_transaction(purpose="oidc_link")


def test_oidc_transaction_requires_and_preserves_client_binding():
    service = OAuthStateService(FakeRedis())
    with pytest.raises(ValueError, match="supported client"):
        service.create_transaction(purpose="oidc_login")

    transaction = service.create_transaction(
        purpose="oidc_login",
        client="mobile",
        nonce="n" * 32,
        code_verifier="v" * 43,
    )

    assert transaction.client == "mobile"
    assert service.consume(transaction.state) == transaction


def test_graph_connect_state_is_user_bound_and_one_time():
    service = OAuthStateService(FakeRedis())
    transaction = service.create_transaction(
        purpose="graph_connect",
        user_id=7,
        next_path="/integrations",
        nonce="n" * 32,
        code_verifier="v" * 43,
    )

    assert transaction.purpose == "graph_connect"
    assert transaction.user_id == 7
    assert service.consume(transaction.state) == transaction
    assert service.consume(transaction.state) is None


@pytest.mark.parametrize(
    "next_path",
    [
        "https://evil.example/steal",
        "//evil.example/steal",
        "\\\\evil.example\\steal",
        "/safe#fragment",
        "/safe\r\nX-Injected: value",
    ],
)
def test_next_path_rejects_external_or_header_injection_values(next_path: str):
    with pytest.raises(ValueError, match="Invalid next path"):
        validate_next_path(next_path)


def test_next_path_accepts_same_origin_path_and_pkce_s256():
    assert validate_next_path(None) == "/"
    assert validate_next_path("/groups/42?tab=chat") == "/groups/42?tab=chat"
    verifier = "a" * 43
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert build_pkce_challenge(verifier) == expected
