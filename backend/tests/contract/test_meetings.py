# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from fastapi.testclient import TestClient
from sqlmodel import Session
from app.main import app
from app.models import User, Meeting
from app.core import security

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
