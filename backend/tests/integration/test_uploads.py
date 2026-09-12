# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from unittest.mock import patch

from fastapi.testclient import TestClient


@patch("app.api.v1.endpoints.meetings.storage")
def test_upload_meeting_file(
    mock_storage, client: TestClient, normal_user_token_headers
):
    """Validate the presign-then-register contract used by the upload client."""
    filename = "test_audio.mp3"
    file_key = "users/1/meetings/test_audio.mp3"
    mock_storage.generate_presigned_upload_url.return_value = (
        "https://storage.example/upload",
        file_key,
    )
    mock_storage.provider_name = "minio"

    presigned_response = client.post(
        "/api/v1/meetings/presigned-upload",
        headers=normal_user_token_headers,
        json={"filename": filename, "content_type": "audio/mpeg"},
    )

    assert presigned_response.status_code == 200, presigned_response.text
    presigned = presigned_response.json()
    assert presigned == {
        "upload_url": "https://storage.example/upload",
        "file_key": file_key,
        "storage_provider": "minio",
    }
    mock_storage.generate_presigned_upload_url.assert_called_once_with(
        user_id=1,
        filename=filename,
        content_type="audio/mpeg",
    )

    meeting_response = client.post(
        "/api/v1/meetings/",
        headers=normal_user_token_headers,
        json={
            "title": "Upload Test",
            "file_key": presigned["file_key"],
            "content_type": "audio/mpeg",
        },
    )

    assert meeting_response.status_code == 200, meeting_response.text
    content = meeting_response.json()
    assert content["title"] == "Upload Test"
    assert content["file_path"] == file_key
    assert content["status"] == "pending_upload"
    assert "id" in content
