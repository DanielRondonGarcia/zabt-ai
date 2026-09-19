# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Endpoint tests for the AI chat route."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
import pytest

from app.api import deps
from app.models import User


@pytest.fixture(name="test_client")
def fixture_test_client() -> Iterator[TestClient]:
    from app.api.v1.endpoints import ai_chat

    app = FastAPI(title="Test AI Chat API")
    app.include_router(ai_chat.router, prefix="/ai-chat", tags=["ai-chat"])

    def override_get_current_active_user() -> User:
        return User(id=5, email="test@example.com", full_name="Test User")

    app.dependency_overrides[
        deps.get_current_active_user
    ] = override_get_current_active_user

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def test_api_v1_router_registers_ai_chat_route() -> None:
    from app.api.api import api_router
    from app.api.v1.endpoints import ai_chat

    assert any(
        route.include_context.prefix == "/ai-chat"
        and route.original_router is ai_chat.router
        for route in api_router.routes
    )


def test_ai_chat_endpoint_validates_and_delegates_to_service(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.v1.endpoints import ai_chat

    calls = []

    class FakeService:
        def chat(self, *, group_id: int, user_id: int, message: str, limit: int):
            calls.append(
                {"group_id": group_id, "user_id": user_id, "message": message, "limit": limit}
            )
            return {
                "group_id": group_id,
                "answer": "answer",
                "sources": [
                    {
                        "meeting_id": 9,
                        "kind": "summary",
                        "chunk_index": 1,
                        "score": 0.88,
                        "text": "source text",
                    }
                ],
            }

    monkeypatch.setattr(ai_chat, "ai_chat_service", FakeService())

    response = test_client.post(
        "/ai-chat/",
        json={"group_id": 12, "message": "  What changed?  ", "limit": 3},
    )

    assert response.status_code == 200, response.text
    assert calls == [
        {"group_id": 12, "user_id": 5, "message": "What changed?", "limit": 3}
    ]
    assert response.json() == {
        "group_id": 12,
        "answer": "answer",
        "sources": [
            {
                "meeting_id": 9,
                "kind": "summary",
                "chunk_index": 1,
                "score": 0.88,
                "text": "source text",
            }
        ],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"group_id": 1, "message": "   "},
        {"group_id": 1, "message": "x", "limit": 0},
        {"group_id": 1, "message": "x", "limit": 21},
        {"group_id": 1, "message": "x" * 4001},
    ],
)
def test_ai_chat_endpoint_rejects_invalid_payload(
    test_client: TestClient, payload: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.v1.endpoints import ai_chat

    class FailService:
        def chat(self, **kwargs):
            pytest.fail("invalid payload must not reach service")

    monkeypatch.setattr(ai_chat, "ai_chat_service", FailService())

    response = test_client.post("/ai-chat/", json=payload)

    assert response.status_code == 422, response.text


def test_foreign_group_403_is_preserved_without_llm_work(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.v1.endpoints import ai_chat

    calls = []

    class FakeService:
        def chat(self, *, group_id: int, user_id: int, message: str, limit: int):
            calls.append((group_id, user_id, message, limit))
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    monkeypatch.setattr(ai_chat, "ai_chat_service", FakeService())

    response = test_client.post(
        "/ai-chat/", json={"group_id": 99, "message": "private", "limit": 8}
    )

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "Forbidden"}
    assert calls == [(99, 5, "private", 8)]


def test_llm_failure_returns_stable_503(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.v1.endpoints import ai_chat

    class FakeService:
        def chat(self, **kwargs):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="chat unavailable",
            )

    monkeypatch.setattr(ai_chat, "ai_chat_service", FakeService())

    response = test_client.post("/ai-chat/", json={"group_id": 1, "message": "hello"})

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "chat unavailable"}
