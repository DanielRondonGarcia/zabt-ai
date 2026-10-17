"""Isolated worker tests for meeting-scoped storage routing."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app.models import Meeting


class FakeStorage:
    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple[str, Any]] = []

    def get_presigned_download_url(self, object_key: str) -> str:
        self.calls.append(("download-url", object_key))
        return f"https://{self.name}/download/{object_key}"

    def delete_file(self, object_key: str) -> None:
        self.calls.append(("delete-file", object_key))


@pytest.fixture
def worker_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embedding-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    import app.worker as worker

    return worker


@pytest.mark.parametrize(
    ("storage_provider", "object_key", "expected_host"),
    [
        ("aws_s3", "users/91/meetings/storage-v1/aws_s3/audio.mp3", "s3"),
        ("minio", "users/91/meetings/legacy-audio.mp3", "minio"),
    ],
)
def test_stage_download_uses_the_meeting_storage_provider(
    worker_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    storage_provider: str,
    object_key: str,
    expected_host: str,
) -> None:
    worker = worker_module
    meeting = Meeting(
        id=41,
        owner_id=91,
        title="Meeting",
        file_path=object_key,
        storage_provider=storage_provider,
    )
    minio = FakeStorage("minio")
    aws = FakeStorage("s3")
    resolved_providers: list[str] = []
    downloaded: list[tuple[str, str]] = []

    def resolve_storage(provider: str) -> FakeStorage:
        resolved_providers.append(provider)
        return aws if provider == "aws_s3" else minio

    monkeypatch.setattr(worker, "storage", minio)
    monkeypatch.setattr(
        worker, "get_storage_for_provider", resolve_storage, raising=False
    )
    monkeypatch.setattr(
        worker, "_required_processing_identity", lambda _task: (7, "attempt-1")
    )
    monkeypatch.setattr(
        worker, "_update_sub_status_for_attempt", lambda *_args: True
    )
    monkeypatch.setattr(worker.meeting_service, "get", lambda *_args: meeting)
    monkeypatch.setattr(worker, "_configured_transcription_provider", lambda: "openai")
    monkeypatch.setattr(
        worker, "_temp_path_for", lambda *_args, **_kwargs: str(tmp_path / "audio.tmp")
    )
    monkeypatch.setattr(
        worker.urllib.request,
        "urlretrieve",
        lambda url, path: downloaded.append((url, path)),
    )

    assert worker.stage_download.run(meeting.id) == meeting.id

    assert resolved_providers == [storage_provider]
    assert downloaded == [
        (f"https://{expected_host}/download/{object_key}", str(tmp_path / "audio.tmp"))
    ]
    selected_storage = aws if storage_provider == "aws_s3" else minio
    other_storage = minio if storage_provider == "aws_s3" else aws
    assert selected_storage.calls == [("download-url", object_key)]
    assert other_storage.calls == []


def test_visual_worker_uses_meeting_storage_for_service_and_rejected_artifact_cleanup(
    worker_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import patch

    worker = worker_module
    meeting_id = 41
    owner_id = 91
    namespace = (
        f"users/{owner_id}/meetings/{meeting_id}/visual/epoch-1/"
        f"standalone-{'a' * 32}"
    )
    raw_key = f"{namespace}/raw_output.json"
    meeting = Meeting(
        id=meeting_id,
        owner_id=owner_id,
        title="AWS meeting",
        file_path="users/91/meetings/storage-v1/aws_s3/video.mp4",
        storage_provider="aws_s3",
        source_type="upload",
        visual_breakdown_params={},
    )
    result = SimpleNamespace(
        status="completed",
        segments=[],
        model="test-model",
        params={},
        raw_output_s3_key=raw_key,
    )
    minio = FakeStorage("minio")
    aws = FakeStorage("s3")
    resolved_providers: list[str] = []

    def resolve_storage(provider: str) -> FakeStorage:
        resolved_providers.append(provider)
        return aws if provider == "aws_s3" else minio

    class FakeRows:
        def all(self) -> list[Any]:
            return []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args):
            return meeting

        def exec(self, *_args):
            return FakeRows()

    monkeypatch.setattr(worker, "storage", minio)
    monkeypatch.setattr(
        worker, "get_storage_for_provider", resolve_storage, raising=False
    )
    monkeypatch.setattr(worker, "Session", lambda *_args, **_kwargs: FakeSession())
    monkeypatch.setattr(
        worker, "_visual_processing_identity", lambda *_args, **_kwargs: (None, None)
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "begin_visual_breakdown",
        lambda *_args, **_kwargs: (1, True),
    )
    monkeypatch.setattr(worker, "_acquire_visual_lease", lambda *_args: True)
    monkeypatch.setattr(
        worker.meeting_service,
        "update_visual_breakdown_progress",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "increment_visual_breakdown_attempt",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        worker.meeting_service,
        "finalize_visual_breakdown",
        lambda *_args, **_kwargs: {"applied": False},
    )
    monkeypatch.setattr(worker, "_emit_visual_side_effects", lambda *_args, **_kwargs: None)

    with patch("app.worker.DirectVisionService") as direct_service:
        direct_service.return_value.submit_and_wait.return_value = result
        assert worker.stage_visual_breakdown.run(meeting_id) == meeting_id

    assert resolved_providers == ["aws_s3"]
    direct_service.assert_called_once_with(storage_provider=aws)
    assert aws.calls == [("delete-file", raw_key)]
    assert minio.calls == []
