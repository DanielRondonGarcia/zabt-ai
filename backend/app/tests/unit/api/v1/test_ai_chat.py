# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Endpoint tests for the AI chat route."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
import pytest

from app.api import deps
from app.models import (
    AIChatConversationDetail,
    AIChatConversationSummary,
    AIChatMessageRead,
    AIChatSourceRead,
    User,
)


@pytest.fixture(name="test_client")
def fixture_test_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from app.api.v1.endpoints import ai_chat

    app = FastAPI(title="Test AI Chat API")
    app.include_router(ai_chat.router, prefix="/ai-chat", tags=["ai-chat"])

    def override_get_current_active_user() -> User:
        return User(id=5, email="test@example.com", full_name="Test User")

    app.dependency_overrides[
        deps.get_current_active_user
    ] = override_get_current_active_user

    class FakeGroupService:
        def __init__(self) -> None:
            self.allowed = True

        def get_accessible(self, group_id: int, user_id: int):
            if not self.allowed:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
            return object()

    group_access = FakeGroupService()
    monkeypatch.setattr(ai_chat, "group_service", group_access)

    with TestClient(app) as client:
        client.app_state = {"group_access": group_access}  # type: ignore[attr-defined]
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
        def chat(
            self,
            *,
            group_id: int,
            user_id: int,
            message: str,
            limit: int,
            conversation_id: int | None = None,
        ):
            calls.append(
                {
                    "group_id": group_id,
                    "user_id": user_id,
                    "message": message,
                    "limit": limit,
                    "conversation_id": conversation_id,
                }
            )
            return {
                "conversation_id": conversation_id or 77,
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
                "evidence_status": "available",
            }

    monkeypatch.setattr(ai_chat, "ai_chat_service", FakeService())

    response = test_client.post(
        "/ai-chat/",
        json={"group_id": 12, "message": "  What changed?  ", "limit": 3},
    )
    follow_up = test_client.post(
        "/ai-chat/",
        json={"group_id": 12, "message": "More?", "conversation_id": 77},
    )

    assert response.status_code == 200, response.text
    assert follow_up.status_code == 200, follow_up.text
    assert calls == [
        {"group_id": 12, "user_id": 5, "message": "What changed?", "limit": 3, "conversation_id": None},
        {"group_id": 12, "user_id": 5, "message": "More?", "limit": 8, "conversation_id": 77},
    ]
    assert follow_up.json()["conversation_id"] == 77
    assert response.json() == {
        "conversation_id": 77,
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
        "evidence_status": "available",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"group_id": 1, "message": "   "},
        {"group_id": 1, "message": "x", "limit": 0},
        {"group_id": 1, "message": "x", "limit": 21},
        {"group_id": 1, "message": "x" * 4001},
        {"group_id": 1, "message": "x", "conversation_id": 0},
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
        def chat(self, *, group_id: int, user_id: int, message: str, limit: int, conversation_id=None):
            calls.append((group_id, user_id, message, limit, conversation_id))
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    monkeypatch.setattr(ai_chat, "ai_chat_service", FakeService())

    response = test_client.post(
        "/ai-chat/", json={"group_id": 99, "message": "private", "limit": 8}
    )

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "Forbidden"}
    assert calls == [(99, 5, "private", 8, None)]


def test_conversation_group_mismatch_409_is_preserved(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.v1.endpoints import ai_chat

    class FakeService:
        def chat(self, **kwargs):
            assert kwargs["conversation_id"] == 4
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conversation belongs to a different group.",
            )

    monkeypatch.setattr(ai_chat, "ai_chat_service", FakeService())

    response = test_client.post(
        "/ai-chat/", json={"group_id": 2, "message": "cross", "conversation_id": 4}
    )

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "Conversation belongs to a different group."}


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


# ── Conversation endpoints ────────────────────────────────────────────────────


class FakeConversationService:
    """Records calls and serves canned conversation data for the endpoint layer."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.summary = AIChatConversationSummary(
            id=3,
            group_id=12,
            title="What changed?",
            created_at=datetime(2026, 10, 6, 12, 0, 0),
            updated_at=datetime(2026, 10, 6, 12, 5, 0),
            message_count=2,
        )

    def list_for_group(self, *, owner_id: int, group_id: int):
        self.calls.append(("list", owner_id, group_id))
        return [self.summary]

    def get_detail(self, conversation_id: int, owner_id: int):
        self.calls.append(("get", conversation_id, owner_id))
        if conversation_id == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
        if conversation_id == 403:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
        return AIChatConversationDetail(
            **self.summary.model_dump(),
            messages=[
                AIChatMessageRead(
                    id=1,
                    role="user",
                    content="What changed?",
                    sources=[],
                    evidence_status=None,
                    created_at=datetime(2026, 10, 6, 12, 0, 0),
                ),
                AIChatMessageRead(
                    id=2,
                    role="assistant",
                    content="A decision was made.",
                    sources=[
                        AIChatSourceRead(meeting_id=9, kind="summary", chunk_index=1, score=0.88, text="src")
                    ],
                    evidence_status="available",
                    created_at=datetime(2026, 10, 6, 12, 5, 0),
                ),
            ],
        )

    def get_owned(self, conversation_id: int, owner_id: int):
        self.calls.append(("authorize", conversation_id, owner_id))
        if conversation_id == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
        if conversation_id == 403:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
        return SimpleNamespace(group_id=12)

    def delete(self, conversation_id: int, owner_id: int) -> None:
        self.calls.append(("delete", conversation_id, owner_id))
        if conversation_id == 403:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")


@pytest.fixture(name="conversations")
def fixture_conversations(monkeypatch: pytest.MonkeyPatch) -> FakeConversationService:
    from app.api.v1.endpoints import ai_chat

    fake = FakeConversationService()
    monkeypatch.setattr(ai_chat, "ai_chat_conversation_service", fake)
    return fake


def test_list_conversations_requires_group_and_scopes_to_current_user(
    test_client: TestClient, conversations: FakeConversationService
) -> None:
    response = test_client.get("/ai-chat/conversations", params={"group_id": 12})

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "id": 3,
            "group_id": 12,
            "title": "What changed?",
            "created_at": "2026-10-06T12:00:00",
            "updated_at": "2026-10-06T12:05:00",
            "message_count": 2,
        }
    ]
    assert conversations.calls == [("list", 5, 12)]
    assert test_client.get("/ai-chat/conversations").status_code == 422
    assert test_client.get("/ai-chat/conversations", params={"group_id": 0}).status_code == 422


def test_get_conversation_returns_ordered_messages_with_evidence_status(
    test_client: TestClient, conversations: FakeConversationService
) -> None:
    response = test_client.get("/ai-chat/conversations/3")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == 3 and body["message_count"] == 2
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["evidence_status"] is None
    assert body["messages"][1]["evidence_status"] == "available"
    assert body["messages"][1]["sources"] == [
        {"meeting_id": 9, "kind": "summary", "chunk_index": 1, "score": 0.88, "text": "src"}
    ]
    assert conversations.calls == [("authorize", 3, 5), ("get", 3, 5)]


@pytest.mark.parametrize(("conversation_id", "expected"), [(404, 404), (403, 403)])
def test_get_conversation_preserves_ownership_errors(
    test_client: TestClient,
    conversations: FakeConversationService,
    conversation_id: int,
    expected: int,
) -> None:
    response = test_client.get(f"/ai-chat/conversations/{conversation_id}")

    assert response.status_code == expected, response.text


def test_delete_conversation_returns_204_and_preserves_403(
    test_client: TestClient, conversations: FakeConversationService
) -> None:
    deleted = test_client.delete("/ai-chat/conversations/3")
    forbidden = test_client.delete("/ai-chat/conversations/403")

    assert deleted.status_code == 204, deleted.text
    assert deleted.content == b""
    assert forbidden.status_code == 403, forbidden.text
    assert conversations.calls == [
        ("authorize", 3, 5),
        ("delete", 3, 5),
        ("authorize", 403, 5),
    ]


def test_detail_and_delete_recheck_revoked_group_access(
    test_client: TestClient, conversations: FakeConversationService
) -> None:
    test_client.app_state["group_access"].allowed = False  # type: ignore[attr-defined]

    detail = test_client.get("/ai-chat/conversations/3")
    deleted = test_client.delete("/ai-chat/conversations/3")

    assert detail.status_code == 403, detail.text
    assert deleted.status_code == 403, deleted.text
    assert conversations.calls == [("authorize", 3, 5), ("authorize", 3, 5)]
