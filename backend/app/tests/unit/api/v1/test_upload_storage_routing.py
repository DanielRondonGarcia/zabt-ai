# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Isolated API tests for binding uploads to their storage provider."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeStorage:
    def __init__(self, provider_kind: str, direct_key: str, multipart_key: str):
        self.provider_name = "s3" if provider_kind == "aws_s3" else provider_kind
        self.direct_key = direct_key
        self.multipart_key = multipart_key
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def generate_presigned_upload_url(self, **kwargs: Any) -> tuple[str, str]:
        self.calls.append(("presign", kwargs))
        return f"https://{self.provider_name}/upload", self.direct_key

    def create_multipart_upload(self, **kwargs: Any) -> tuple[str, str]:
        self.calls.append(("initiate", kwargs))
        return "upload-aws-1", self.multipart_key

    def generate_part_url(
        self,
        object_key: str,
        upload_id: str,
        part_number: int,
        expiration: int = 3600,
    ) -> str:
        self.calls.append(
            (
                "part-url",
                {
                    "object_key": object_key,
                    "upload_id": upload_id,
                    "part_number": part_number,
                    "expiration": expiration,
                },
            )
        )
        return f"https://{self.provider_name}/part/{part_number}"

    def complete_multipart_upload(
        self, *, object_key: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> None:
        self.calls.append(
            (
                "complete",
                {"object_key": object_key, "upload_id": upload_id, "parts": parts},
            )
        )


@pytest.fixture
def project_modules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embedding-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    from app.api import deps
    from app.api.v1.endpoints import meetings, uploads
    from app.models import Meeting, MeetingCreate, User
    from app.services import storage as storage_service
    from app.services.meeting import MeetingService

    return {
        "deps": deps,
        "meetings": meetings,
        "uploads": uploads,
        "Meeting": Meeting,
        "MeetingCreate": MeetingCreate,
        "User": User,
        "storage_service": storage_service,
        "MeetingService": MeetingService,
    }


@pytest.fixture
def upload_client(
    project_modules, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(project_modules["meetings"].router, prefix="/api/v1/meetings")
    app.include_router(project_modules["uploads"].router, prefix="/api/v1")

    user = project_modules["User"](
        id=101,
        email="upload-test@example.com",
        supabase_id="upload-test-user",
        full_name="Upload Test",
    )
    app.dependency_overrides[
        project_modules["deps"].get_current_active_user
    ] = lambda: user

    def get_test_db():
        yield object()

    app.dependency_overrides[project_modules["deps"].get_db] = get_test_db
    monkeypatch.setattr(
        project_modules["meetings"].lang_prefs,
        "get_primary_code",
        lambda *_args: None,
    )

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def test_direct_upload_and_meeting_creation_keep_the_server_selected_provider(
    upload_client: TestClient,
    project_modules,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meetings = project_modules["meetings"]
    minio = FakeStorage(
        "minio",
        "users/101/meetings/storage-v1/minio/minio-audio.mp3",
        "users/101/meetings/storage-v1/minio/minio-chunks.m4a",
    )
    aws = FakeStorage(
        "aws_s3",
        "users/101/meetings/storage-v1/aws_s3/aws-audio.mp3",
        "users/101/meetings/storage-v1/aws_s3/aws-chunks.m4a",
    )
    monkeypatch.setattr(meetings, "storage", minio)
    monkeypatch.setattr(meetings, "get_current_storage", lambda _db: aws, raising=False)

    saved_providers: list[str] = []

    def create_meeting(
        meeting_in,
        owner_id: int,
        storage_provider: str = "minio",
    ):
        saved_providers.append(storage_provider)
        return project_modules["Meeting"](
            id=len(saved_providers),
            title=meeting_in.title,
            description=meeting_in.description,
            file_path=meeting_in.file_path,
            owner_id=owner_id,
            storage_provider=storage_provider,
        )

    monkeypatch.setattr(meetings.meeting_service, "create_meeting", create_meeting)

    presigned = upload_client.post(
        "/api/v1/meetings/presigned-upload",
        json={"filename": "audio.mp3", "content_type": "audio/mpeg"},
    )
    assert presigned.status_code == 200, presigned.text
    assert presigned.json() == {
        "upload_url": "https://s3/upload",
        "file_key": aws.direct_key,
        "storage_provider": "s3",
    }

    registered = upload_client.post(
        "/api/v1/meetings/",
        json={"title": "AWS upload", "file_key": aws.direct_key},
    )
    assert registered.status_code == 200, registered.text
    assert registered.json()["file_path"] == aws.direct_key
    assert saved_providers == ["aws_s3"]

    legacy = upload_client.post(
        "/api/v1/meetings/",
        json={"title": "Legacy MinIO upload", "file_key": "legacy/audio.mp3"},
    )
    assert legacy.status_code == 200, legacy.text
    assert saved_providers == ["aws_s3", "minio"]


def test_multipart_upload_routes_each_step_by_the_server_generated_key_marker(
    upload_client: TestClient,
    project_modules,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploads = project_modules["uploads"]
    minio = FakeStorage(
        "minio",
        "users/101/meetings/storage-v1/minio/minio-audio.mp3",
        "users/101/meetings/storage-v1/minio/minio-chunks.m4a",
    )
    aws = FakeStorage(
        "aws_s3",
        "users/101/meetings/storage-v1/aws_s3/aws-audio.mp3",
        "users/101/meetings/storage-v1/aws_s3/aws-chunks.m4a",
    )
    monkeypatch.setattr(uploads, "storage", minio, raising=False)
    monkeypatch.setattr(uploads, "get_current_storage", lambda _db: aws, raising=False)
    resolved_providers: list[str] = []

    def resolve_storage(provider: str, _db: Any) -> FakeStorage:
        resolved_providers.append(provider)
        return aws if provider == "aws_s3" else minio

    monkeypatch.setattr(
        uploads, "get_storage_for_provider", resolve_storage, raising=False
    )
    monkeypatch.setattr(
        uploads,
        "storage_provider_from_object_key",
        project_modules["storage_service"].storage_provider_from_object_key,
        raising=False,
    )

    initiated = upload_client.post(
        "/api/v1/uploads/initiate",
        json={"filename": "chunks.m4a", "content_type": "audio/mp4", "total_parts": 2},
    )
    assert initiated.status_code == 200, initiated.text
    body = initiated.json()
    assert body["s3_key"] == aws.multipart_key
    assert body["upload_id"] == "upload-aws-1"
    assert [part["url"] for part in body["part_urls"]] == [
        "https://s3/part/1",
        "https://s3/part/2",
    ]

    part_url = upload_client.post(
        "/api/v1/uploads/part-url",
        json={"s3_key": aws.multipart_key, "upload_id": "upload-aws-1", "part_number": 3},
    )
    assert part_url.status_code == 200, part_url.text
    assert part_url.json() == {"url": "https://s3/part/3"}

    completed = upload_client.post(
        "/api/v1/uploads/complete",
        json={
            "s3_key": aws.multipart_key,
            "upload_id": "upload-aws-1",
            "parts": [
                {"part_number": 2, "etag": "etag-2"},
                {"part_number": 1, "etag": "etag-1"},
            ],
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json() == {"s3_key": aws.multipart_key}
    assert resolved_providers == ["aws_s3", "aws_s3"]
    assert [call for call in aws.calls if call[0] == "complete"] == [
        (
            "complete",
            {
                "object_key": aws.multipart_key,
                "upload_id": "upload-aws-1",
                "parts": [
                    {"PartNumber": 1, "ETag": "etag-1"},
                    {"PartNumber": 2, "ETag": "etag-2"},
                ],
            },
        )
    ]
    assert minio.calls == []


def test_legacy_multipart_flow_routes_to_minio_when_aws_is_active(
    upload_client: TestClient,
    project_modules,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploads = project_modules["uploads"]
    legacy_key = "users/101/meetings/legacy-audio.m4a"
    minio = FakeStorage("minio", "unused", "unused")
    aws = FakeStorage("aws_s3", "unused", "unused")
    resolved_providers: list[str] = []

    def resolve_storage(provider: str, _db: Any) -> FakeStorage:
        resolved_providers.append(provider)
        return minio if provider == "minio" else aws

    monkeypatch.setattr(
        uploads, "get_storage_for_provider", resolve_storage, raising=False
    )
    monkeypatch.setattr(
        uploads,
        "storage_provider_from_object_key",
        project_modules["storage_service"].storage_provider_from_object_key,
        raising=False,
    )

    part_url = upload_client.post(
        "/api/v1/uploads/part-url",
        json={"s3_key": legacy_key, "upload_id": "legacy-upload", "part_number": 3},
    )
    assert part_url.status_code == 200, part_url.text
    assert part_url.json() == {"url": "https://minio/part/3"}

    completed = upload_client.post(
        "/api/v1/uploads/complete",
        json={
            "s3_key": legacy_key,
            "upload_id": "legacy-upload",
            "parts": [
                {"part_number": 2, "etag": "etag-2"},
                {"part_number": 1, "etag": "etag-1"},
            ],
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json() == {"s3_key": legacy_key}
    assert resolved_providers == ["minio", "minio"]
    assert [name for name, _kwargs in minio.calls] == ["part-url", "complete"]
    assert minio.calls[-1][1]["parts"] == [
        {"PartNumber": 1, "ETag": "etag-1"},
        {"PartNumber": 2, "ETag": "etag-2"},
    ]
    assert aws.calls == []


def test_empty_multipart_completion_returns_400_without_resolving_storage(
    upload_client: TestClient,
    project_modules,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploads = project_modules["uploads"]
    resolved_providers: list[str] = []
    monkeypatch.setattr(
        uploads,
        "get_storage_for_provider",
        lambda provider, _db: resolved_providers.append(provider),
        raising=False,
    )

    response = upload_client.post(
        "/api/v1/uploads/complete",
        json={
            "s3_key": "users/101/meetings/storage-v1/aws_s3/audio.m4a",
            "upload_id": "upload-1",
            "parts": [],
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "at least one part required"}
    assert resolved_providers == []


def test_unknown_provider_marker_is_a_client_error_without_side_effects(
    upload_client: TestClient,
    project_modules,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meetings = project_modules["meetings"]
    uploads = project_modules["uploads"]
    invalid_key = "users/101/meetings/storage-v1/unknown/audio.mp3"
    created_meetings: list[object] = []
    resolved_providers: list[str] = []

    monkeypatch.setattr(
        meetings.meeting_service,
        "create_meeting",
        lambda *args, **kwargs: created_meetings.append((args, kwargs)),
    )
    monkeypatch.setattr(
        uploads,
        "get_storage_for_provider",
        lambda provider, _db: resolved_providers.append(provider),
        raising=False,
    )

    meeting_response = upload_client.post(
        "/api/v1/meetings/",
        json={"title": "Invalid marker", "file_key": invalid_key},
    )
    assert meeting_response.status_code == 400
    assert meeting_response.json() == {
        "detail": "Unsupported storage provider marker in object key."
    }
    assert created_meetings == []

    part_response = upload_client.post(
        "/api/v1/uploads/part-url",
        json={"s3_key": invalid_key, "upload_id": "upload-1", "part_number": 1},
    )
    assert part_response.status_code == 400
    assert part_response.json() == {
        "detail": "Unsupported storage provider marker in object key."
    }

    complete_response = upload_client.post(
        "/api/v1/uploads/complete",
        json={
            "s3_key": invalid_key,
            "upload_id": "upload-1",
            "parts": [{"part_number": 1, "etag": "etag-1"}],
        },
    )
    assert complete_response.status_code == 400
    assert complete_response.json() == {
        "detail": "Unsupported storage provider marker in object key."
    }
    assert resolved_providers == []


def test_meeting_service_persists_explicit_provider_and_defaults_to_minio(
    project_modules, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = project_modules["MeetingService"]()
    monkeypatch.setattr(service, "save", lambda meeting: meeting)

    aws_meeting = service.create_meeting(
        project_modules["MeetingCreate"](title="AWS", file_path="aws-key"),
        owner_id=101,
        storage_provider="aws_s3",
    )
    minio_meeting = service.create_meeting(
        project_modules["MeetingCreate"](title="Legacy", file_path="legacy-key"),
        owner_id=101,
    )

    assert aws_meeting.storage_provider == "aws_s3"
    assert minio_meeting.storage_provider == "minio"
