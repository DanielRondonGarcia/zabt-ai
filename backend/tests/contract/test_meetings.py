# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from app.main import app
from app.models import User, Meeting
from app.core import security
from app.api.v1.endpoints.meetings import _normalize_media_type

def test_create_meeting(client: TestClient, db: Session, normal_user_token_headers):
    data = {
        "title": "Test Meeting",
        "description": "Integration Test",
        "file_key": "users/1/meetings/test/audio.mp3",
        "content_type": "audio/mpeg",
    }
    response = client.post(
        "/api/v1/meetings/",
        headers=normal_user_token_headers,
        json=data,
    )
    assert response.status_code == 200
    content = response.json()
    assert content["title"] == data["title"]
    assert content["description"] == data["description"]
    assert "id" in content
    assert "owner_id" in content

def test_read_meeting(client: TestClient, db: Session, normal_user_token_headers):
    # predefined meeting or create one
    data = {
        "title": "Read Test",
        "description": "Read Me",
        "file_key": "users/1/meetings/read/audio.mp3",
        "content_type": "audio/mpeg",
    }
    create_res = client.post("/api/v1/meetings/", headers=normal_user_token_headers, json=data)
    meeting_id = create_res.json()["id"]

    response = client.get(f"/api/v1/meetings/{meeting_id}", headers=normal_user_token_headers)
    assert response.status_code == 200
    content = response.json()
    assert content["title"] == "Read Test"


def test_create_meeting_keeps_legacy_contract_with_optional_visual_fields(
    client: TestClient, normal_user_token_headers
):
    """Visual metadata is additive; transcript-only consumers keep their fields."""
    response = client.post(
        "/api/v1/meetings/",
        headers=normal_user_token_headers,
        json={
            "title": "Compatibility Meeting",
            "description": "Transcript-only contract",
            "file_key": "users/1/meetings/compatibility/audio.mp3",
            "content_type": "audio/mpeg",
        },
    )

    assert response.status_code == 200, response.text
    content = response.json()
    assert content["title"] == "Compatibility Meeting"
    assert content["summary_text"] is None
    assert content["segments"] == []
    assert content["visual_breakdown_status"] is None
    assert content["visual_breakdown_error"] is None


def test_meeting_response_exposes_building_context_before_completion(
    client: TestClient, db: Session, normal_user_token_headers
):
    """The summary context stage remains processing until intelligence finishes."""
    meeting = Meeting(
        owner_id=1,
        title="Context ordering",
        file_path="users/1/meetings/context/audio.mp4",
        status="processing",
        sub_status="building_context",
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    response = client.get(
        f"/api/v1/meetings/{meeting.id}",
        headers=normal_user_token_headers,
    )

    assert response.status_code == 200, response.text
    content = response.json()
    assert content["status"] == "processing"
    assert content["sub_status"] == "building_context"


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"content_type": "audio/mpeg"}, "audio"),
        ({"content_type": " VIDEO/MP4 ; codecs=hvc1 "}, "video"),
        ({}, None),
        ({"content_type": "  "}, None),
        ({"content_type": "application/octet-stream"}, None),
        (None, None),
    ],
)
def test_normalize_media_type_handles_known_and_legacy_mime_values(params, expected):
    assert _normalize_media_type(params) == expected


@pytest.mark.parametrize(
    ("content_type", "expected_params", "expected_media_type"),
    [
        (" VIDEO/MP4 ; codecs=hvc1 ", {"content_type": "video/mp4"}, "video"),
        (" AUDIO/MPEG ", {"content_type": "audio/mpeg"}, "audio"),
        ("application/octet-stream", {"content_type": "application/octet-stream"}, None),
        ("", {"content_type": ""}, None),
        (None, None, None),
    ],
)
def test_create_meeting_persists_normalized_mime_and_nullable_media_type(
    client: TestClient,
    db: Session,
    normal_user_token_headers,
    content_type,
    expected_params,
    expected_media_type,
):
    payload = {
        "title": "MIME contract",
        "description": "MIME normalization",
        "file_key": "users/1/meetings/mime/media.bin",
    }
    if content_type is not None:
        payload["content_type"] = content_type

    response = client.post(
        "/api/v1/meetings/",
        headers=normal_user_token_headers,
        json=payload,
    )

    assert response.status_code == 200, response.text
    content = response.json()
    meeting = db.get(Meeting, content["id"])
    assert meeting is not None
    assert meeting.visual_breakdown_params == expected_params
    assert content["media_type"] == expected_media_type


def test_read_meeting_preserves_signed_audio_url_and_normalizes_media_type(
    client: TestClient,
    normal_user_token_headers,
    monkeypatch,
):
    signed_url = "https://storage.example.test/signed/video.mp4"
    monkeypatch.setattr(
        "app.api.v1.endpoints.meetings._signed_download_url",
        lambda object_key, **kwargs: signed_url,
    )

    create_response = client.post(
        "/api/v1/meetings/",
        headers=normal_user_token_headers,
        json={
            "title": "Video detail",
            "file_key": "users/1/meetings/detail/video.mp4",
            "content_type": "video/mp4",
        },
    )
    assert create_response.status_code == 200, create_response.text

    response = client.get(
        f"/api/v1/meetings/{create_response.json()['id']}",
        headers=normal_user_token_headers,
    )

    assert response.status_code == 200, response.text
    content = response.json()
    assert content["media_type"] == "video"
    assert content["audio_url"] == signed_url


def test_list_meetings_exposes_nullable_media_type_from_jsonb(
    client: TestClient,
    normal_user_token_headers,
):
    create_response = client.post(
        "/api/v1/meetings/",
        headers=normal_user_token_headers,
        json={
            "title": "Video list",
            "file_key": "users/1/meetings/list/video.mp4",
            "content_type": "video/mp4",
        },
    )
    assert create_response.status_code == 200, create_response.text
    meeting_id = create_response.json()["id"]

    response = client.get(
        "/api/v1/meetings/",
        headers=normal_user_token_headers,
    )

    assert response.status_code == 200, response.text
    content = next(item for item in response.json() if item["id"] == meeting_id)
    assert content["media_type"] == "video"
