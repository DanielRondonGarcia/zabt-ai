# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from app.db.engine import engine
from app.models import User
from app.core import security
from app.core.config import settings
from app.services.transcription import get_provider
from app.services.transcription.errors import UnsupportedCapabilityError
from app.services.meeting import meeting_service
import time

router = APIRouter()


def _header_bearer_token(websocket: WebSocket) -> str | None:
    authorization = websocket.headers.get("authorization")
    if not authorization:
        return None

    parts = authorization.split()
    if len(parts) != 2 or parts[0].casefold() != "bearer":
        raise ValueError("Invalid WebSocket authorization header")
    return parts[1]


def _require_allowed_origin(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin", "").rstrip("/")
    if origin not in security.allowed_origins():
        raise ValueError("Invalid WebSocket origin")


@router.websocket("/ws/{meeting_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    meeting_id: int,
    token: str | None = Query(default=None),
):
    await websocket.accept()

    # Authenticate
    try:
        header_token = _header_bearer_token(websocket)
        cookie_token = websocket.cookies.get(settings.AUTH_ACCESS_COOKIE_NAME)
        if header_token:
            raw_token = header_token
        elif cookie_token:
            _require_allowed_origin(websocket)
            raw_token = cookie_token
        else:
            # Query-string tokens are retained for legacy clients only. They
            # require an allowed Origin because the URL may be logged by a
            # browser, proxy, or WebSocket server.
            _require_allowed_origin(websocket)
            raw_token = token
        payload = security.verify_websocket_token(raw_token)
        user_id = int(payload["sub"])
        with Session(engine) as db:
            user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise ValueError("Inactive or unknown user")
        meeting = meeting_service.get_meeting(meeting_id)
        if meeting is None or meeting.owner_id != user_id:
            raise ValueError("Meeting not found")
    except Exception:
        await websocket.close(code=1008)
        return

    provider = None

    try:
        provider = get_provider()
        if not getattr(provider, "capabilities", None) or not provider.capabilities.realtime:
            raise UnsupportedCapabilityError(
                "realtime",
                getattr(provider, "provider_name", "unknown"),
                getattr(provider, "model", None),
                "The configured transcription provider is batch-only; realtime is not implemented",
            )
        while True:
            # Check for binary audio data
            data = await websocket.receive_bytes()

            # Start timer
            start_time = time.time()

            # Transcribe via provider abstraction
            text = await provider.transcribe_chunk(data)

            end_time = time.time()

            if text:
                # Save segment
                segment = meeting_service.add_segment(
                    meeting_id=meeting_id,
                    start=start_time,
                    end=end_time,
                    text=text,
                )

                # Send back to client
                await websocket.send_json({
                    "text": text,
                    "segment_id": segment.id,
                    "is_final": True,
                })

    except UnsupportedCapabilityError as exc:
        await websocket.close(code=1003, reason=str(exc))
    except WebSocketDisconnect:
        print(f"Client disconnected from meeting {meeting_id}")
    except Exception as e:
        print(f"WebSocket error: {e}")
        await websocket.close()
    finally:
        if provider is not None:
            provider.close()
