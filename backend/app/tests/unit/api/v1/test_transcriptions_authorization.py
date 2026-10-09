# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded WebSocket authorization checks for realtime transcript writes."""

from types import SimpleNamespace

from fastapi import HTTPException, WebSocketDisconnect
import pytest

from app.models import User


class FakeWebSocket:
    def __init__(self) -> None:
        self.headers = {"authorization": "Bearer test-token"}
        self.cookies: dict[str, str] = {}
        self.sent: list[dict] = []
        self.closed: list[tuple[int | None, str | None]] = []
        self.received = False

    async def accept(self) -> None:
        return None

    async def receive_bytes(self) -> bytes:
        if self.received:
            raise WebSocketDisconnect()
        self.received = True
        return b"audio"

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.closed.append((code, reason))


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, model, user_id: int):
        return User(id=user_id, email="editor@example.com", is_active=True)


class FakeProvider:
    capabilities = SimpleNamespace(realtime=True)
    provider_name = "test"
    model = "test-model"

    def __init__(self, text: str = "hello") -> None:
        self.text = text
        self.closed = False

    async def transcribe_chunk(self, data: bytes) -> str:
        assert data == b"audio"
        return self.text

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_websocket_checks_editor_at_handshake_and_before_each_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.endpoints import transcriptions

    websocket = FakeWebSocket()
    provider = FakeProvider()
    checks: list[tuple[int, int]] = []
    segments: list[dict] = []

    monkeypatch.setattr(
        transcriptions.security,
        "verify_websocket_token",
        lambda token: {"sub": "2"},
    )
    monkeypatch.setattr(transcriptions, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(
        transcriptions.meeting_service,
        "require_editor_for_user",
        lambda meeting_id, user_id: checks.append((meeting_id, user_id)),
    )
    monkeypatch.setattr(transcriptions, "get_provider", lambda: provider)
    monkeypatch.setattr(
        transcriptions.meeting_service,
        "add_segment_for_user",
        lambda **kwargs: segments.append(kwargs) or SimpleNamespace(id=7),
    )

    await transcriptions.websocket_endpoint(websocket, meeting_id=10)

    assert checks == [(10, 2), (10, 2)]
    assert segments[0]["user_id"] == 2
    assert websocket.sent == [{"text": "hello", "segment_id": 7, "is_final": True}]
    assert provider.closed is True


@pytest.mark.asyncio
async def test_websocket_closes_with_policy_error_when_membership_is_revoked_before_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.endpoints import transcriptions

    websocket = FakeWebSocket()
    provider = FakeProvider()

    monkeypatch.setattr(
        transcriptions.security,
        "verify_websocket_token",
        lambda token: {"sub": "2"},
    )
    monkeypatch.setattr(transcriptions, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(
        transcriptions.meeting_service,
        "require_editor_for_user",
        lambda meeting_id, user_id: None,
    )
    monkeypatch.setattr(transcriptions, "get_provider", lambda: provider)
    monkeypatch.setattr(
        transcriptions.meeting_service,
        "add_segment_for_user",
        lambda **kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="Editor access is required.")
        ),
    )

    await transcriptions.websocket_endpoint(websocket, meeting_id=10)

    assert websocket.sent == []
    assert websocket.closed == [(1008, "Editor access is required.")]
    assert provider.closed is True
