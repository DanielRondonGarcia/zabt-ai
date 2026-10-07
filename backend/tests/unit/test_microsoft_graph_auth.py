# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Focused tests for the delegated Microsoft Graph OAuth boundary."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints import integrations as integrations_endpoint
from app.core.config import settings
from app.models import User
from app.services.microsoft_graph import MicrosoftGraphError
from app.services.oauth_state import OAuthStateTransaction


class FakeStateService:
    def __init__(self, transaction: OAuthStateTransaction):
        self.transaction = transaction
        self.consume_count = 0
        self.create_kwargs = None

    def create_transaction(self, **kwargs):
        self.create_kwargs = kwargs
        return self.transaction

    def consume(self, state):
        self.consume_count += 1
        if self.consume_count > 1 or state != self.transaction.state:
            return None
        return self.transaction


class FakeGraphClient:
    def __init__(self, *, failure: Exception | None = None):
        self.failure = failure
        self.auth_args = None
        self.exchange_args = []
        self.profile_calls = 0

    def build_auth_url(self, state, code_challenge=None):
        self.auth_args = {"state": state, "code_challenge": code_challenge}
        return "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?state=opaque"

    async def exchange_code(self, code, code_verifier=None):
        self.exchange_args.append((code, code_verifier))
        if self.failure is not None:
            raise self.failure
        return {
            "access_token": "graph-access-token",
            "refresh_token": "graph-refresh-token",
            "expires_in": 3600,
            "scope": "Calendars.Read User.Read",
        }

    async def get_user_profile(self, access_token):
        self.profile_calls += 1
        assert access_token == "graph-access-token"
        return {"id": "graph-user-id", "email": "user@example.com"}


class FakeDb:
    def __init__(self, user: User | None):
        self.user = user

    def get(self, model, user_id):
        if model is User and self.user is not None and self.user.id == user_id:
            return self.user
        return None


@dataclass
class GraphHarness:
    state: FakeStateService
    client: FakeGraphClient
    db: FakeDb
    upserts: list[dict]


def make_user(user_id: int = 7, *, is_active: bool = True) -> User:
    return User(
        id=user_id,
        email="user@example.com",
        full_name="Graph User",
        password_hash="hash",
        is_active=is_active,
    )


def make_harness(*, user_id: int = 7, next_path: str = "/integrations") -> GraphHarness:
    transaction = OAuthStateTransaction(
        state="opaque-graph-state-123456789012345678901234",
        purpose="graph_connect",
        nonce="n" * 32,
        code_verifier="v" * 43,
        next_path=next_path,
        user_id=user_id,
    )
    return GraphHarness(
        state=FakeStateService(transaction),
        client=FakeGraphClient(),
        db=FakeDb(make_user(user_id)),
        upserts=[],
    )


@pytest.fixture(autouse=True)
def graph_configuration(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "MICROSOFT_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "MICROSOFT_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "MICROSOFT_REDIRECT_URI", "https://api.example/graph/callback")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "APP_URL", "https://app.example")


def test_connect_creates_user_bound_graph_state_and_pkce_url(monkeypatch: pytest.MonkeyPatch):
    harness = make_harness()
    monkeypatch.setattr(integrations_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(integrations_endpoint, "_get_graph_client", lambda: harness.client)

    response = integrations_endpoint.connect_provider("microsoft", make_user())

    assert response.auth_url.startswith("https://login.microsoftonline.com/")
    assert harness.state.create_kwargs == {
        "purpose": "graph_connect",
        "user_id": 7,
        "next_path": "/integrations",
    }
    assert harness.client.auth_args == {
        "state": harness.state.transaction.state,
        "code_challenge": integrations_endpoint.build_pkce_challenge("v" * 43),
    }


@pytest.mark.asyncio
async def test_callback_consumes_state_once_and_upserts_only_state_owner(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = make_harness(user_id=7)
    monkeypatch.setattr(integrations_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(integrations_endpoint, "_get_graph_client", lambda: harness.client)
    monkeypatch.setattr(
        integrations_endpoint.integration_service,
        "upsert_from_oauth",
        lambda **kwargs: harness.upserts.append(kwargs),
    )

    response = await integrations_endpoint.oauth_callback(
        "microsoft",
        db=harness.db,
        code="authorization-code",
        state=harness.state.transaction.state,
        error=None,
    )
    replay = await integrations_endpoint.oauth_callback(
        "microsoft",
        db=harness.db,
        code="authorization-code",
        state=harness.state.transaction.state,
        error=None,
    )

    assert response.headers["location"] == "https://app.example/integrations?connected=microsoft"
    assert replay.headers["location"] == "https://app.example/integrations?microsoft_error=state"
    assert harness.client.exchange_args == [("authorization-code", "v" * 43)]
    assert len(harness.upserts) == 1
    assert harness.upserts[0]["user_id"] == 7
    assert harness.upserts[0]["provider_user_id"] == "graph-user-id"


@pytest.mark.asyncio
async def test_callback_provider_denial_is_static_and_does_not_exchange_or_upsert(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = make_harness()
    monkeypatch.setattr(integrations_endpoint, "oauth_state_service", harness.state)

    response = await integrations_endpoint.oauth_callback(
        "microsoft",
        db=harness.db,
        state=harness.state.transaction.state,
        error="access_denied",
    )

    assert response.headers["location"] == "https://app.example/integrations?microsoft_error=cancelled"
    assert harness.client.exchange_args == []
    assert harness.upserts == []


def test_graph_oversized_code_is_not_reflected_by_fastapi_or_sent_to_provider(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = make_harness()
    marker = "OVERSIZED_GRAPH_AUTH_CODE_MARKER"
    monkeypatch.setattr(integrations_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(integrations_endpoint, "_get_graph_client", lambda: harness.client)

    def override_db():
        yield harness.db

    test_app = FastAPI()
    test_app.include_router(integrations_endpoint.router, prefix="/integrations")
    test_app.dependency_overrides[integrations_endpoint.get_db] = override_db
    with TestClient(test_app) as client:
        response = client.get(
            "/integrations/microsoft/callback",
            params={"state": harness.state.transaction.state, "code": marker * 200},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert marker not in response.text
    assert marker not in response.headers["location"]
    assert harness.client.exchange_args == []


@pytest.mark.parametrize(
    "missing_setting",
    [
        "MICROSOFT_CLIENT_ID",
        "MICROSOFT_CLIENT_SECRET",
        "MICROSOFT_REDIRECT_URI",
        "TOKEN_ENCRYPTION_KEY",
    ],
)
def test_connect_missing_graph_or_storage_configuration_fails_before_state_or_redirect(
    monkeypatch: pytest.MonkeyPatch,
    missing_setting: str,
):
    harness = make_harness()
    monkeypatch.setattr(integrations_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(settings, missing_setting, "")

    with pytest.raises(HTTPException) as exc_info:
        integrations_endpoint.connect_provider("microsoft", make_user())

    assert exc_info.value.status_code == 503
    assert "TOKEN_ENCRYPTION_KEY" not in str(exc_info.value)
    assert harness.state.create_kwargs is None


@pytest.mark.asyncio
async def test_callback_invalid_configuration_is_safe_after_state_consumption(
    monkeypatch: pytest.MonkeyPatch,
):
    harness = make_harness()
    monkeypatch.setattr(integrations_endpoint, "oauth_state_service", harness.state)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")

    response = await integrations_endpoint.oauth_callback(
        "microsoft",
        db=harness.db,
        code="authorization-code",
        state=harness.state.transaction.state,
        error=None,
    )

    assert response.headers["location"] == "https://app.example/integrations?microsoft_error=configuration"
    assert harness.state.consume_count == 1


@pytest.mark.asyncio
async def test_callback_does_not_connect_inactive_or_different_user(
    monkeypatch: pytest.MonkeyPatch,
):
    inactive = make_harness(user_id=7)
    inactive.db = FakeDb(make_user(7, is_active=False))
    monkeypatch.setattr(integrations_endpoint, "oauth_state_service", inactive.state)

    response = await integrations_endpoint.oauth_callback(
        "microsoft",
        db=inactive.db,
        code="authorization-code",
        state=inactive.state.transaction.state,
        error=None,
    )
    assert response.headers["location"] == "https://app.example/integrations?microsoft_error=account"

    different_user = make_harness(user_id=7)
    different_user.db = FakeDb(make_user(8))
    monkeypatch.setattr(integrations_endpoint, "oauth_state_service", different_user.state)
    response = await integrations_endpoint.oauth_callback(
        "microsoft",
        db=different_user.db,
        code="authorization-code",
        state=different_user.state.transaction.state,
        error=None,
    )
    assert response.headers["location"] == "https://app.example/integrations?microsoft_error=account"
